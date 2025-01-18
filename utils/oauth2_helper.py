import logging
import time
import re
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
            new_token, new_token_expire = self.refresher(self.refresh_token)
            logger.info('refreshed token %s, next expiration %s' % (self.refresh_token, new_token_expire))
            self.access_token, self.access_token_expire = new_token, new_token_expire
        return self.access_token
    
    def getSasl(self, username):
        saslBody = f"user={username}".encode() + b"\x01" + f"auth=Bearer {self.getToken()}".encode() +  b"\x01\x01"
        return b64encode(saslBody).decode()


class TokenStore():
    store: dict[str, Token] = {}
    
    @classmethod
    def get(self, refresh_token: str, refresher) -> Token:
        if refresh_token not in self.store:
            self.store[refresh_token] = Token(refresh_token, refresher)
        return self.store[refresh_token]    

class OAuth2_Base():
    name: str
    token_uri: str
    client_id: str
    redirect_uri: str
    
    suffix_list: list[str]
    @classmethod
    def can_handle_email(self, email):
        for suffix in self.suffix_list:
            if email.endswith(suffix):
                return True
        return False

    @classmethod
    def get_login_url(self, email):
        raise NotImplementedError()
    
    @classmethod
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
    
    @classmethod
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
    PROVIDERS_DICT: dict[str, OAuth2_Base] = {}
    
    @classmethod
    def register_provider(self, provider):
        self.PROVIDERS_DICT[provider.name] = provider

    @classmethod
    def get_provider(self, name):
        if name not in self.PROVIDERS_DICT:
            raise RuntimeError('invalid provider name %s, available providers: %s' % (name, list(self.PROVIDERS_DICT.keys())))
        return self.PROVIDERS_DICT[name]

    @classmethod
    def detect_provider(self, email):
        for name, provider in self.PROVIDERS_DICT.items():
            if provider.can_handle_email(email):
                return name

    @classmethod
    def token_from_string(self, s) -> Token | None:
        # s should have format token:{provider}:{refresh_token}
        if not s.startswith('token:'):
            return None
        m = re.match(r'^token:(?P<provider_name>.*?):(?P<refresh_token>.*)$', s)
        if not m:
            raise ValueError('invalid token string: %s, should have format token:{provider}:{refresh_token}' % s)
        provider_name, refresh_token = m.groups()
        
        provider = self.get_provider(provider_name)
        return TokenStore.get(refresh_token, provider.access_token_from_refresh_token)
    
    @classmethod
    def code_to_token(self, s) -> str | None:
        if not s.startswith('code:'):
            return None
        # s should have format token:{provider}:{refresh_token}
        m = re.match(r'^code:(?P<provider_name>.*?):(?P<refresh_token>.*)$', s)
        if not m:
            raise ValueError('invalid code string: %s, should have format token:{provider}:{refresh_token}' % s)
        provider_name, code = m.groups()
        
        provider = self.get_provider(provider_name)
        refresh_token, token, token_expire = provider.refresh_token_from_code(code)
        t = TokenStore.get(refresh_token, provider.access_token_from_refresh_token)
        t.access_token = token
        t.access_token_expire = token_expire
        return f'token:{provider_name}:{refresh_token}'

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