"""Constants for the ЕИРЦ СПб integration."""
from datetime import timedelta

DOMAIN = "eirc_spb"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SESSION_COOKIE = "session_cookie"
CONF_TOKEN_AUTH = "token_auth"
CONF_TOKEN_VERIFY = "token_verify"
CONF_SELECTED_ACCOUNTS = "selected_accounts"

API_BASE_URL = "https://ikus.pesc.ru/api"
COOKIE_URL = f"{API_BASE_URL}/v6/users/manual/existence"
AUTH_URL = f"{API_BASE_URL}/v8/users/auth"
ACCOUNTS_URL = f"{API_BASE_URL}/v8/accounts"
METERS_INFO_URL = f"{API_BASE_URL}/v6/accounts/{{account_id}}/meters/info"
READING_PERIOD_URL = f"{API_BASE_URL}/v6/accounts/{{account_id}}/reading/period"
CURRENT_BILL_URL = f"{API_BASE_URL}/v8/accounts/{{account_id}}/payments/bills/current"
SUBMIT_READING_URL = (
    f"{API_BASE_URL}/v8/accounts/{{account_id}}/meters/{{registration}}/reading"
)
