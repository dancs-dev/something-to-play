import json
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings
from openai import OpenAI

from app.llm import OpenAIProvider, ProviderError


@override_settings(OLLAMA_BASE_URL='http://localhost:11434/v1', OLLAMA_MODEL='qwen3.5:latest',
                   OPENAI_API_KEY='hosted-secret')
class OllamaTests(SimpleTestCase):
    def sdk_client(self, data, finish_reason='stop', refusal=None):
        def respond(request):
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer ollama')
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'qwen3.5:latest')
            self.assertEqual(payload['response_format']['type'], 'json_schema')
            self.assertNotIn('"maxLength"', json.dumps(payload['response_format']))
            self.assertNotIn('tools', payload)
            self.assertNotIn('store', payload)
            return httpx.Response(200, json={
                'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 0, 'model': 'qwen3.5:latest',
                'choices': [{'index': 0, 'finish_reason': finish_reason,
                             'message': {'role': 'assistant', 'content': json.dumps(data), 'refusal': refusal}}]})
        return OpenAI(base_url='http://localhost:11434/v1', api_key='ollama', max_retries=0,
                      http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    def test_sdk_uses_local_endpoint_and_never_forwards_hosted_key(self):
        with patch('app.llm.OpenAI') as constructor:
            provider = OpenAIProvider()
        self.assertTrue(provider.ollama)
        self.assertEqual(constructor.call_args.kwargs['base_url'], 'http://localhost:11434/v1')
        self.assertEqual(constructor.call_args.kwargs['api_key'], 'ollama')
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)

    @override_settings(OPENAI_API_KEY='')
    def test_extraction_and_discovery_work_without_real_key_or_hosted_tools(self):
        with self.sdk_client({'preferences': [{'kind': 'mechanic', 'subject': 'puzzles', 'sentiment': 1,
                                          'reason': 'Enjoy solving them', 'confidence': .9}]}) as client:
            result = OpenAIProvider(client=client).extract_preferences(['I like puzzles'])
            self.assertEqual(result.preferences[0].subject, 'puzzles')
        with self.sdk_client({'candidates': [{'appid': 620, 'title': 'Portal 2'}]}) as client:
            result = OpenAIProvider(client=client).discover_candidates({'session': {}, 'preferences': []})
            self.assertEqual(result.candidates[0].appid, 620)

    def test_invalid_truncated_and_refused_local_output_fails_closed(self):
        for data, finish, refusal in [({'preferences': 'bad'}, 'stop', None),
                                      ({'preferences': [{'kind': 'game', 'subject': 'x' * 201,
                                          'sentiment': 1, 'reason': '', 'confidence': 1.0}]}, 'stop', None),
                                      ({'preferences': []}, 'length', None),
                                      ({'preferences': []}, 'stop', 'Cannot comply')]:
            with self.subTest(finish=finish, refusal=refusal), self.sdk_client(data, finish, refusal) as client:
                with self.assertRaises(ProviderError):
                    OpenAIProvider(client=client).extract_preferences(['user text'])

    def test_local_connection_failure_is_a_provider_error(self):
        def unavailable(request):
            raise httpx.ConnectError('local service unavailable')
        with OpenAI(api_key='ollama', base_url='http://localhost:11434/v1', max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(unavailable))) as client:
            with self.assertRaises(ProviderError):
                OpenAIProvider(client=client).extract_preferences(['user text'])
