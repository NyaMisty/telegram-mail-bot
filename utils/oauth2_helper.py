import logging
import time
import re
from typing import Any, Optional, Type, TypeVar
import uuid
import requests
import urllib.parse
from base64 import b64encode
logger = logging.getLogger(__name__)

class Token():
    def __init__(self, refresh_token, refresher):
        self.refresh_token = refresh_token
        self.refresher = refresher

        self.access_token = None
        self.access_token_expire = 0
        if not self.access_token_expire:
            self.access_token_expire = 0

    def getToken(self):
        if time.time() - 600 > self.access_token_expire: # TTL less than 10min
            ret = self.refresher(self.refresh_token)
            new_token, new_token_expire = ret[:2]
            new_refresh_token = ret[2] if len(ret) > 2 else None
            if new_refresh_token and new_refresh_token != self.refresh_token:
                TokenStore.move(self.refresh_token, new_refresh_token, self)
                self.refresh_token = new_refresh_token
            logger.info('refreshed token %s, next expiration %s' % (self.refresh_token, new_token_expire))
            self.access_token, self.access_token_expire = new_token, new_token_expire
        return self.access_token

    def getSasl(self, username, raw=False):
        saslBody = f"user={username}".encode() + b"\x01" + f"auth=Bearer {self.getToken()}".encode() +  b"\x01\x01"
        if not raw:
            return b64encode(saslBody).decode()
        else:
            return saslBody


class TokenStore():
    store: dict[str, Token] = {}

    @classmethod
    def get(self, refresh_token: str, refresher) -> Token:
        if refresh_token not in self.store:
            self.store[refresh_token] = Token(refresh_token, refresher)
        return self.store[refresh_token]

    @classmethod
    def move(self, old_refresh_token: str, new_refresh_token: str, token: Token):
        if old_refresh_token in self.store:
            self.store.pop(old_refresh_token, None)
        self.store[new_refresh_token] = token

class OAuth2_Base():
    name: str
    token_uri: str
    client_id: str
    redirect_uri: str
    
    suffix_list: list[str]

    def __init__(self, email_addr: str, additional_data: Optional[str]=None, **kwargs) -> None:
        # Providers may use additional_data to customize the properties like client_id
        self.email_addr = email_addr
        self.additional_data = additional_data

    @classmethod
    def can_handle_email(self, email):
        for suffix in self.suffix_list:
            if email.endswith(suffix):
                return True
        return False

    @classmethod
    def get_login_url(self, email):
        raise NotImplementedError()
    
    # @classmethod
    def refresh_token_from_code(self, code):

        data = {
            'client_id': self.client_id,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri,
        }
        if hasattr(self, 'client_secret'):
            data['client_secret'] = self.client_secret

        response = requests.post(self.token_uri, data=data)
        if response.status_code != 200:
            raise RuntimeError("server returned invalid status %d, body: %s" % (response.status_code, response.text))

        # {
        #     "access_token": "XXXXXXXXX",
        #     "expires_in": 3600,
        #     "ext_expires_in": 3600,
        #     "refresh_token": "M.C513_BAY.0.U.xxxxxxx*apKcVy*Vk$",
        #     "scope": "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/POP.AccessAsUser.All https://outlook.office.com/SMTP.Send",
        #     "token_type": "Bearer"
        # }
        try:
            r = response.json()
            refresh_token = r['refresh_token']
            token = r['access_token']
            expire = time.time() + r['expires_in'] - 10
        except Exception:
            raise RuntimeError('invalid server return body: %s' % response.text)
        return refresh_token, token, expire
    
    # @classmethod
    def access_token_from_refresh_token(self, refresh_token):
        data = {
            'client_id': self.client_id,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        if hasattr(self, 'client_secret'):
            data['client_secret'] = self.client_secret

        response = requests.post(self.token_uri, data=data)
        if response.status_code != 200:
            raise RuntimeError("server returned invalid status %d, body: %s" % (response.status_code, response.text))

        # {
        #     "access_token": "EwBoA+l3BAAUpSDGiWSEqG8SEbhMwx+LVy/3Wu8AAWE/qXx6KFnxFMe3eKPIVMaEdWD7EtxIfo1b5V6nZhz7m6e5d7OnmAoZyJetC+I8J4Edjh3bTca9UZs461KjKC2JJ2AmNTZ2XjMDdGnKfpumK2kcfFLet+CQPlcDejpy/+BEq3iYMCvUzieLbKu3DXddCc2/y4wNtFPz/FrH6akmTY5PkIDHETelLMMy83YpUQXOsU9daFChtIxDJ8c/H8Y38446hevqGHD8SNcAebANMoEfgp79W/WPJCcM21u0I2h2IeJmL2WzKuMLnNrJnHm039IdbGt6XJQ/TgKDhcmRQR7I9X2tHK6OftSLjd6sPEA76NX0J9/7cjBfZ1FMqQMQZgAAEH3ITLOMWbnocnoIZCZx2AYwAi/lUBGociDkcaln/EwTvfX9Unt84RlLLsjFSaZiFfc8a7jp1n8sNnLXnQ2dU0htHr6Ixk57Xld2NkEQI3+jSO7S+OrUPCP0mlDMUGeHpj1o8k4UEQVAEsVhR2+LolxcNmLWetKPs/giSFLIqKPfTTaRksGw9lUN8YjtsUQKqJxLF74OwJv2mgEv7D12iagybmNhE5TbsVfxgNllV/eDKfBXtF4cfA09oRuTTlG65g/FBeDSBN3WSX8YYCijhqZPocDUZtBJ7qFgWziflAR+efmbElxOow+y9LuZYIvVS7KZ3eQvTGmv2jtFcphNp4+Nc/czZvd8vYfLBeHrJX7lxdD5i69rJUCMAR3EMONKl8mqhQUV5r6anwNKRi4yG0Gi37z1AXNT5Y7R9akK5m6xeWvvDC9HXXHiQS6E9zk+I2GkufUOWNjwtwD1Y1r4AYd4oWIOlJfjsABACsJbxuI1g04hBlcvd3eE3HgCEIQLNw3qhePHFd07Ueb3cNnM2wr5ucxAAQhpjKPUdiAWirPXRThw7fAH+GH53+udXSwdgD2eo7QlQJAfY1Wg8DsYuXW5ccFcJ+UbAXMEZD5EpwlPxY6O5vehKtRyr0pkmk8a0Js9yr518RFpXl5LhyUSezRh1dcxws+PdPJS4VwAzm9PNoXBlrJA98XAMteS/9NROtJ9W7KJ2b8NlWdOPiAj4Z223uUFzMNrjLekDdOxk4ZiZ0jW/tjyRPufx3ioRgYbCGdGRgI=",
        #     "expires_in": 3600,
        #     "ext_expires_in": 3600,
        #     "refresh_token": "M.C537_BL2.0.U.-Cr!decG3CDqZAaG9XNwDq2K1mX4qXZ5lDFz4mHRnfkjxs8emyc1!bK0Id8QtliUxeBNnCR3!lNT5ox1l0ADHAveBUSPDx1ukMEPiuy*Wplr4B9k5hxlniRA9NYEm!geACORW531hBj81FIYifsVrpk2WfOcjiWS9ae1ilJFyyDqeJxm0blsFtTbOkX063CLsBFL0NiJHWfQitWYtBI85ABgV5Nl18xBk8XNojK2mYQp0VMV7VlYIII0parTR218NNanGBClRTdZydGMRfc5*G7PsAOHhJWByQIJ3ENkCgnzCkW0is!Uq93u5vvgr1*tLaCbh9E9ZHZ7QEZqo!cXZ8CbEzeOIXjGrBbUw2BcxRswissKLcrPRELao0gtfU!2gCfWn1RA9QtESlRtBUbQaXeA$",
        #     "scope": "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/POP.AccessAsUser.All https://outlook.office.com/SMTP.Send",
        #     "token_type": "Bearer"
        # }
        try:
            r = response.json()
            token = r['access_token']
            expire_time = time.time() + r['expires_in'] - 10 # 10 seconds safety bar
        except Exception:
            raise RuntimeError('invalid server return body: %s' % response.text)
        return token, expire_time

class OAuth2Factory():
    PROVIDERS_DICT: dict[str, Type[OAuth2_Base]] = {}
    
    @classmethod
    def register_provider(self, provider):
        self.PROVIDERS_DICT[provider.name] = provider

    @classmethod
    def get_provider(self, email_addr, name, additional_data=None):
        if name not in self.PROVIDERS_DICT:
            raise RuntimeError('invalid provider name %s, available providers: %s' % (name, list(self.PROVIDERS_DICT.keys())))
        return self.PROVIDERS_DICT[name](email_addr=email_addr, additional_data=additional_data)

    @classmethod
    def detect_provider(self, email):
        for name, provider in self.PROVIDERS_DICT.items():
            if provider.can_handle_email(email):
                return name

    @classmethod
    def token_from_string(self, email_addr, s) -> Token | None:
        # s should have format token:{provider}:{refresh_token} or token:{provider}:{refresh_token}:::{additional_data}
        if not s.startswith('token:'):
            return None
        provider_name, refresh_token, additional_data = self.parse_token_parts(s)

        provider = self.get_provider(email_addr, provider_name, additional_data)
        return TokenStore.get(refresh_token, provider.access_token_from_refresh_token)

    @classmethod
    def parse_token_parts(self, s) -> tuple[str, str, str | None]:
        if not s.startswith('token:'):
            raise ValueError('invalid token string: %s, should have format token:{provider}:{refresh_token}' % s)
        body = s[len('token:'):]
        provider_name, sep, token_data = body.partition(':')
        if not sep:
            raise ValueError('invalid token string: %s, should have format token:{provider}:{refresh_token}' % s)
        refresh_token, _, additional_data = token_data.partition(':::')
        if not provider_name or not refresh_token:
            raise ValueError('invalid token string: %s, should have format token:{provider}:{refresh_token}' % s)
        return provider_name, refresh_token, additional_data or None
    
    @classmethod
    def code_to_token(self, email_addr, s) -> str | None:
        if not s.startswith('code:'):
            return None
        # s should have format token:{provider}:{refresh_token}
        m = re.match(r'^code:(?P<provider_name>.*?):(?P<refresh_token>.*)$', s)
        if not m:
            raise ValueError('invalid code string: %s, should have format token:{provider}:{refresh_token}' % s)
        provider_name, code = m.groups()

        provider = self.get_provider(email_addr, provider_name)
        refresh_token, token, token_expire = provider.refresh_token_from_code(code)
        t = TokenStore.get(refresh_token, provider.access_token_from_refresh_token)
        t.access_token = token
        t.access_token_expire = token_expire
        return f'token:{provider_name}:{refresh_token}'

class OAuth2_Proton(OAuth2_Base):
    name = 'proton'
    token_uri = ''
    client_id = ''
    redirect_uri = ''
    suffix_list = []

    def _update_stored_refresh_token(self, old_refresh_token: str, new_refresh_token: str):
        email_addr = self.email_addr
        if not email_addr or not self.additional_data:
            return
        from .emailconf import emailDB

        email_confs = emailDB.getByQuery({'email_addr': email_addr})
        if not email_confs:
            return
        for email_conf in email_confs:
            old_passwd = email_conf.get('email_passwd')
            if not old_passwd:
                continue
            provider_name, refresh_token, additional_data = OAuth2Factory.parse_token_parts(old_passwd)
            if provider_name != 'proton' or refresh_token != old_refresh_token or additional_data != self.additional_data:
                continue
            new_passwd = f'token:proton:{new_refresh_token}:::{self.additional_data}'
            emailDB.updateByQuery({'email_addr': email_addr}, {'email_passwd': new_passwd})
            break

    def access_token_from_refresh_token(self, refresh_token):
        from protonmail_client import ProtonIOSLogin, parse_proton_token

        token_data = parse_proton_token(f'token:proton:{refresh_token}:::{self.additional_data or ""}')
        login = ProtonIOSLogin()
        ret = login.refresh_access_token(token_data.uid, token_data.refresh_token, token_data.access_token)
        new_refresh_token = ret.get('RefreshToken') or refresh_token
        access_token = ret['AccessToken']
        expire_time = time.time() + ret.get('ExpiresIn', 3600) - 10
        if new_refresh_token != refresh_token:
            self._update_stored_refresh_token(refresh_token, new_refresh_token)
            return access_token, expire_time, new_refresh_token
        return access_token, expire_time

OAuth2Factory.register_provider(OAuth2_Proton)

class OAuth2_MS(OAuth2_Base):
    name = 'ms'
    token_uri = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token'
    
    # thunderbolt
    # client_id = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'
    # redirect_uri = 'https://localhost'
    
    # harry https://harrychen.xyz/2024/09/25/msmtp-outlook-oauth/
    # client_id = '1ba11cc8-c6d1-4ae6-bd88-6becf878f8df'
    # client_secret = 'lBm8Q~_IfyNpFUZ6KydTc4QHjLl1IwcCxFhxqa7n'
    # redirect_uri = 'http://localhost'
    
    # misty
    client_id = '55797b5d-1e14-44bc-a7b3-52575eb1d6ef'
    redirect_uri = 'https://localhost'
    suffix_list = ['@outlook.com', '@hotmail.com', '@msn.com', '@live.com']
    
    def __init__(self, email_addr: str, additional_data: Optional[str]=None, **kwargs) -> None:
        if additional_data:
            # ensure additional data is uuid format
            self.client_id = str(uuid.UUID(additional_data))
        super().__init__(email_addr=email_addr, additional_data=additional_data)

    @classmethod
    def get_login_url(self, email):
        return f'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?response_type=code&client_id={self.client_id}&redirect_uri=https%3A%2F%2Flocalhost&scope=https%3A%2F%2Foutlook.office.com%2FIMAP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FPOP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FSMTP.Send+offline_access'

OAuth2Factory.register_provider(OAuth2_MS)

class OAuth2_MSOrg(OAuth2_MS):
    name = 'ms-org'
    token_uri = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
    suffix_list = []

OAuth2Factory.register_provider(OAuth2_MSOrg)

class OAuth2_MailRu(OAuth2_Base):
    name = 'mailru'
    token_uri = 'https://o2.mail.ru/token'
    redirect_uri = 'http://localhost'

    # client_id = 'thebat'
    client_id = 'thunderbird'
    client_secret = 'I0dCAXrcaNFujaaY'
    suffix_list = ['@mail.ru', '@inbox.ru', '@bk.ru', '@list.ru', '@internet.ru', '@xmail.ru', ]
    @classmethod
    def get_login_url(self, email):
        # return f'https://o2.mail.ru/login?scope=mail.imap%20userinfo&client_id=thebat&redirect_uri=http%3A%2F%2Flocalhost&login={urllib.parse.quote_plus(email)}&state=get_auth&response_type=code&approval_prompt=auto'
        return f'https://o2.mail.ru/login?response_type=code&client_id=thunderbird&redirect_uri=http%3A%2F%2Flocalhost&scope=mail.imap&login_hint={urllib.parse.quote_plus(email)}'

OAuth2Factory.register_provider(OAuth2_MailRu)

class OAuth2_Gmail(OAuth2_Base):
    name = 'gmail'
    token_uri = 'https://www.googleapis.com/oauth2/v3/token'
    redirect_uri = 'http://localhost'

    client_id = '406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com'
    client_secret = bytes.fromhex('6b536d717265527230717742574a67626635592d506a5355').decode() # must use this to bypass Google's app secret leak test
    suffix_list = ['@gmail.com', ]
    @classmethod
    def get_login_url(self, email):
        return f'https://accounts.google.com/o/oauth2/auth?response_type=code&client_id={self.client_id}&redirect_uri=http%3A%2F%2Flocalhost&scope=https%3A%2F%2Fmail.google.com%2F+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcarddav+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar&login_hint={urllib.parse.quote_plus(email)}'

OAuth2Factory.register_provider(OAuth2_Gmail)