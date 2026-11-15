"""Sending an alert somewhere a person will see it.

One webhook, two body shapes. "text" posts the message as the plain body with
a ``Title`` header, which is what ntfy.sh expects: point it at
``https://ntfy.sh/<a topic only you know>`` and install the ntfy app to get a
phone push, with no account. "json" posts ``{"text": ..., "content": ...}``,
which Slack ("text") and Discord ("content") incoming webhooks both accept.

With no URL configured, alerts are logged at ERROR and nothing is sent.
"""

import logging
import unicodedata

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, url: str = "", body_format: str = "text",
                 timeout_seconds: float = 10.0) -> None:
        self._url = url.strip()
        self._format = body_format
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._url)

    async def send(self, title: str, message: str) -> bool:
        """Deliver one alert. True when it was delivered.

        Always logs at ERROR, so an unconfigured or failing webhook still leaves
        the alert in the service log.
        """
        logger.error("ALERT: %s: %s", title, message)
        if not self.configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if self._format == "json":
                    text = "{}\n{}".format(title, message)
                    response = await client.post(
                        self._url, json={"text": text, "content": text}
                    )
                else:
                    response = await client.post(
                        self._url,
                        content=message.encode("utf-8"),
                        # Header values must be ASCII; "São Paulo" -> "Sao Paulo".
                        headers={"Title": _ascii(title)},
                    )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("alert webhook failed, will retry next check: %s", exc)
            return False
        return True


def _ascii(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text)
    return plain.encode("ascii", "ignore").decode("ascii")
