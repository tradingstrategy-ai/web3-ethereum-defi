import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.response import HTTPResponse

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class RPCMonitoringAdapter(HTTPAdapter):
    def build_response(self, req, resp):
        response = super().build_response(req, resp)

        # Check if it's a POST response
        if req.method == "POST":
            try:
                # Parse the response content
                content = response.json()

                # Define your condition to check for specific payload content
                if self._should_log_warning(content):
                    logger.warning(f"Detected specific payload in POST response: {content}")
            except (ValueError, KeyError):
                # Handle cases where response isn't JSON or doesn't have expected keys
                pass

        return response

    def _should_log_warning(self, content):
        """
        Define your condition to identify responses that should trigger warnings
        """
        # Example: Check if response contains specific error code or message
        if isinstance(content, dict):
            # Example conditions - customize these based on the payload patterns you want to catch
            if content.get("error_code") == "RATE_LIMIT_EXCEEDED":
                return True
            if content.get("status") == "failed" and content.get("reason") == "authentication_error":
                return True
        return False


# Usage example
def create_monitored_session():
    session = requests.Session()
    adapter = ResponseMonitoringAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# Example use
if __name__ == "__main__":
    session = create_monitored_session()
    response = session.post("https://api.example.com/data", json={"key": "value"})
    # The adapter will automatically log warnings for responses matching your criteria
