import base64
import logging
import secrets
import time
from http.cookies import SimpleCookie
from requests import HTTPError, RequestException

from protonmail_client import ProtonMailClient, build_proton_token, parse_proton_token
from protonmail_client.auth import APP_VERSION, USER_AGENT, redact_api_data

from .client_base import EmailClientBase, testMain
from .mail import Email
from .oauth2_helper import OAuth2Factory, TokenStore

logger = logging.getLogger(__name__)


class NotifyingProtonAuthManager:
    def __init__(self, email_account: str, token_payload: str, token_data):
        self.uid = token_data.uid
        self.access_token = token_data.access_token
        self.refresh_token = token_data.refresh_token
        self.auth_time = token_data.auth_time
        self.login_password = token_data.login_password
        self.auth_mode = token_data.auth_mode
        self.token_identifier = f'proton:{email_account}:{token_data.auth_time}'
        self.token_payload = token_payload

    def apply_headers(self, session):
        session.headers.update({
            'x-pm-appversion': APP_VERSION,
            'user-agent': USER_AGENT,
        })
        if self.uid:
            session.headers['x-pm-uid'] = self.uid
        if self.access_token:
            session.headers['authorization'] = f'Bearer {self.access_token}'

    def refresh(self, session, api_url: str) -> bool:
        if not self.uid or not self.refresh_token:
            raise RuntimeError('ProtonMail refresh requires uid and refresh_token')
        old_auth = session.headers.get('authorization')
        old_access_token = self.access_token
        old_token_payload = self.token_payload
        data = {
            'UID': self.uid,
            'RefreshToken': self.refresh_token,
            'ResponseType': 'token',
            'GrantType': 'refresh_token',
            'RedirectURI': 'https://protonmail.ch',
            'State': secrets.token_urlsafe(32),
        }
        if self.access_token:
            data['AccessToken'] = self.access_token
        try:
            last_error = None
            for attempt in range(3):
                try:
                    response = session.post(f'{api_url}/auth/v4/refresh', json=data)
                    break
                except RequestException as e:
                    last_error = e
                    logger.warning('ProtonMail refresh network error, retry %d/3', attempt + 1)
                    if attempt == 2:
                        raise
                    time.sleep(1)
            else:
                raise last_error
            if old_auth and response.status_code >= 400:
                session.headers['authorization'] = old_auth
            response.raise_for_status()
            ret = response.json()
            if ret.get('Code') != 1000:
                raise RuntimeError(f'ProtonMail refresh error: {redact_api_data(ret)}')
            self.uid = ret.get('UID') or self.uid
            self.access_token = ret['AccessToken']
            self.refresh_token = ret.get('RefreshToken') or self.refresh_token
            self.apply_headers(session)
            _, new_token_payload, _ = OAuth2Factory.parse_token_parts(self.current_token())
            self.token_payload = new_token_payload
            if old_access_token != self.access_token or old_token_payload != new_token_payload:
                TokenStore.notify_token_update(self.token_identifier, old_access_token, self.access_token, old_token_payload, new_token_payload)
            return True
        except Exception:
            if old_auth:
                session.headers['authorization'] = old_auth
            raise

    def current_token(self) -> str:
        if not self.uid or not self.access_token or not self.refresh_token or not self.auth_time or not self.login_password:
            raise RuntimeError('ProtonMail token requires uid, access_token, refresh_token, auth_time and login_password')
        return build_proton_token(self.uid, self.access_token, self.refresh_token, self.auth_time, self.login_password)


class ProtonCookieAuthManager:
    uid = None
    access_token = None
    refresh_token = None
    auth_time = None

    def __init__(self, cookie: str, login_password: str | None):
        self.cookie = cookie
        self.login_password = login_password
        self.auth_mode = 'cookie'

    def apply_headers(self, session):
        session.headers.update({
            'x-pm-appversion': 'web-mail@5.0.112.4',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        })
        session.headers['cookie'] = self.cookie
        cookie = SimpleCookie()
        cookie.load(self.cookie)
        for name in cookie.keys():
            if name.startswith('AUTH-'):
                self.uid = name[len('AUTH-'):]
                session.headers['x-pm-uid'] = self.uid
                break

    def refresh(self, session, api_url: str) -> bool:
        return False

    def current_token(self) -> str:
        raise RuntimeError('Cookie auth cannot be exported as OAuth Proton token')


class EmailClientProton(EmailClientBase):
    def __init__(self, email_account, passwd, server_uri=None):
        self.email_account = email_account
        self.password = passwd
        self.server_uri = server_uri
        self.client = self._create_client(passwd)

    def _create_client(self, passwd: str) -> ProtonMailClient:
        logger.debug('Creating Proton client: account=%s server_uri=%s token_prefix=%s',
                     self.email_account, self.server_uri, passwd.split(':', 2)[:2])
        if passwd.startswith('proton:cookie64:'):
            logger.debug('Creating Proton cookie64 client')
            return self._create_cookie_client(passwd[len('proton:cookie64:'):], decode_base64=True)
        if passwd.startswith('proton:cookie:'):
            logger.debug('Creating Proton cookie client')
            return self._create_cookie_client(passwd[len('proton:cookie:'):], decode_base64=False)
        try:
            token_data = parse_proton_token(passwd)
            _, token_payload, _ = OAuth2Factory.parse_token_parts(passwd)
            auth_manager = NotifyingProtonAuthManager(self.email_account, token_payload, token_data)
            client = ProtonMailClient(self.email_account, server_uri=self.server_uri, auth_manager=auth_manager)
            logger.debug('ProtonMailClient ready: api_url=%s uid=%s auth_mode=%s access_present=%s refresh_prefix=%s',
                         getattr(client, 'api_url', None), getattr(client, 'uid', None), getattr(client, 'auth_mode', None),
                         bool(getattr(client, 'access_token', None)),
                         getattr(client, 'refresh_token', '')[:8] if getattr(client, 'refresh_token', None) else None)
            return client
        except HTTPError as e:
            logger.warning('Proton client creation failed: status=%s url=%s',
                           e.response.status_code if e.response is not None else None,
                           e.response.url if e.response is not None else None)
            raise

    def _create_cookie_client(self, payload: str, decode_base64: bool) -> ProtonMailClient:
        pieces = payload.split(':')
        if not pieces or not pieces[0]:
            raise ValueError('empty proton cookie payload')
        cookie_header = pieces[0]
        if decode_base64:
            padding = '=' * (-len(cookie_header) % 4)
            cookie_header = base64.urlsafe_b64decode((cookie_header + padding).encode()).decode()
        extras = pieces[1:]
        if not extras:
            raise ValueError('proton cookie token must include login_password')
        login_password = extras[0] if len(extras) == 1 else ':'.join(extras[1:])
        auth_manager = ProtonCookieAuthManager(cookie_header, login_password)
        return ProtonMailClient(
            self.email_account,
            server_uri=self.server_uri,
            auth_manager=auth_manager,
        )

    def get_mailboxes(self) -> list[str]:
        logger.debug('Proton get_mailboxes start')
        result = self.client.get_mailboxes()
        logger.debug('Proton get_mailboxes done: %s', result)
        return result

    def get_mails_countmap(self, mailboxes: list[str]) -> dict[str, int]:
        logger.debug('Proton get_mails_countmap start: mailboxes=%s', mailboxes)
        result = self.client.get_mails_countmap(mailboxes)
        logger.debug('Proton get_mails_countmap done: %s', result)
        return result

    def get_mail_by_index(self, index, mailbox='inbox'):
        logger.debug('Proton get_mail_by_index start: index=%s mailbox=%s', index, mailbox)
        result = Email(self.client.get_mail_by_index(index, mailbox=mailbox))
        logger.debug('Proton get_mail_by_index done: index=%s mailbox=%s', index, mailbox)
        return result

    def refresh_connection(self):
        logger.debug('Proton refresh_connection start')
        self.client.refresh_connection()
        logger.debug('Proton refresh_connection done')

    def cleanup(self):
        self.client.cleanup()

    def kill(self):
        self.client.kill()


if __name__ == '__main__':
    testMain(EmailClientProton)
