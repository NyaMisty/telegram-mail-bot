import base64
import logging
from requests import HTTPError

from protonmail_client import (
    ProtonBearerAuthManager,
    ProtonCookieAuthManager,
    ProtonMailClient,
    ProtonTokenData,
    parse_proton_token,
)

from .client_base import EmailClientBase, testMain
from .mail import Email
from .oauth2_helper import OAuth2Factory, TokenStore

logger = logging.getLogger(__name__)


class NotifyingProtonBearerAuthManager(ProtonBearerAuthManager):
    def __init__(self, email_account: str, token_payload: str, token_data: ProtonTokenData):
        super().__init__(token_data)
        self.token_identifier = f'proton:{email_account}:{token_data.auth_time}'
        self.token_payload = token_payload

    def refresh(self, session, api_url: str) -> bool:
        old_access_token = self.access_token
        old_token_payload = self.token_payload
        ret = super().refresh(session, api_url)
        _, new_token_payload, _ = OAuth2Factory.parse_token_parts(self.current_token())
        self.token_payload = new_token_payload
        if old_access_token != self.access_token or old_token_payload != new_token_payload:
            TokenStore.notify_token_update(self.token_identifier, old_access_token, self.access_token, old_token_payload, new_token_payload)
        return ret


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
            auth_manager = NotifyingProtonBearerAuthManager(self.email_account, token_payload, token_data)
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
        if len(extras) == 1:
            refresh_token = None
            login_password = extras[0]
        else:
            refresh_token = extras[0] or None
            login_password = ':'.join(extras[1:])
        token_data = ProtonTokenData(
            cookie=cookie_header,
            refresh_token=refresh_token,
            login_password=login_password,
            auth_mode='cookie',
        )
        auth_manager = ProtonCookieAuthManager(token_data)
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
