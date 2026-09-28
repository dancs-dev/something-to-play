import re

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def populate_normalized_titles(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    Game = apps.get_model("app", "Game")
    games = Game.objects.using(schema_editor.connection.alias)
    batch = []
    for game in games.iterator(chunk_size=1000):
        game.normalized_title = " ".join(re.findall(r"\w+", game.title.casefold()))
        batch.append(game)
        if len(batch) == 1000:
            games.bulk_update(batch, ["normalized_title"])
            batch.clear()
    if batch:
        games.bulk_update(batch, ["normalized_title"])


class Migration(migrations.Migration):
    dependencies = [("app", "0006_alter_preference_sentiment")]

    operations = [
        migrations.AddField(
            model_name="game",
            name="normalized_title",
            field=models.TextField(db_index=True, default="", editable=False),
        ),
        migrations.RunPython(populate_normalized_titles, migrations.RunPython.noop),
    ]
