function showLocalTimes(root) {
  root.querySelectorAll('time[data-local-time]').forEach((element) => {
    const date = new Date(element.dateTime);
    if (Number.isNaN(date.getTime())) return;
    const options = {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
    };
    if (element.dataset.localTime !== 'short') options.year = 'numeric';
    element.textContent = new Intl.DateTimeFormat(undefined, options).format(date);
  });
}

showLocalTimes(document);
document.body.addEventListener('htmx:afterSwap', (event) => showLocalTimes(event.target));
