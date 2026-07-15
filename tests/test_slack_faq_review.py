import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from slack_faq_review import Slack  # noqa: E402


def test_history_formats_oldest_at_slack_timestamp_precision():
    class RecordingSlack(Slack):
        def __init__(self):
            super().__init__("unused")
            self.params = None

        def call(self, method, **params):
            self.params = params
            return {"messages": [], "response_metadata": {}}

    api = RecordingSlack()

    api.history("C123", 1783504696.1234567)

    assert api.params["oldest"] == "1783504696.123457"
