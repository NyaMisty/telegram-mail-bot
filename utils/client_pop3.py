import logging
import os
import poplib
import time
from urllib.parse import urlparse, ParseResult
import socks # type: ignore
from .client_base import EmailClientBase, testMain
from .oauth2_helper import OAuth2Factory, Token
from utils.mail import Email

def _create_socket_proxy(self, timeout):
    import socket
    # use pysocks to connect through socks5://1.2.3.4:3333
    
    if timeout is not None and not timeout:
        raise ValueError('Non-blocking socket (timeout=0) is not supported')
    POP3_PROXY = os.getenv('POP3_PROXY')
    if POP3_PROXY:
        pop3Proxy = urlparse(POP3_PROXY)
        proxyType = {
            "socks5": socks.PROXY_TYPE_SOCKS5,
            "socks4": socks.PROXY_TYPE_SOCKS4,
            "http": socks.PROXY_TYPE_HTTP,
        }[pop3Proxy.scheme]
        s = socks.create_connection(
            (self.host, self.port), timeout,
            proxy_type=proxyType,
            proxy_addr=pop3Proxy.hostname,
            proxy_port=pop3Proxy.port,
            proxy_username=pop3Proxy.username,
            proxy_password=pop3Proxy.password,
        )
    else:
        s = socket.create_connection((self.host, self.port), timeout)
    return s
        
poplib.POP3._create_socket = _create_socket_proxy # type: ignore

logger = logging.getLogger(__name__)

class EmailClientPOP3(EmailClientBase):
    def __init__(self, email_account, passwd, server_uri=None):
        self.email_account = email_account
        self.password = passwd
        if not server_uri:
            server_uri = 'pop3s://pop.'+self.email_account.split('@')[-1]
        self.server_uri: ParseResult = urlparse(server_uri) # type: ignore
        self.server = self.connect()

    def connect(self):
        # parse the server's hostname from email account
        if self.server_uri.scheme == 'pop3s':
            server = poplib.POP3_SSL(self.server_uri.hostname, self.server_uri.port or poplib.POP3_SSL_PORT)
        else:
            # TODO: implement pop3 starttls
            raise RuntimeError(f'Unsupported POP3 protocol variant: {self.server_uri.scheme}')

        if os.getenv('POP3DEBUG'):
            server.set_debuglevel(100)
        
        # display the welcome info received from server,
        # indicating the connection is set up properly
        logger.info('pop3 server welcome: %s', server.getwelcome().decode('utf8'))
        # authenticating
        token: Token = OAuth2Factory.token_from_string(self.password)
        if token is None:
            # normal basic auth
            server.user(self.email_account)
            server.pass_(self.password)
        else:
            saslBody = token.getSasl(self.email_account)
            server._shortcmd('AUTH XOAUTH2')
            server._shortcmd(saslBody)
        return server

    def get_mails_list(self):
        _, mails, _ = self.server.list()
        return mails

    def get_mails_countmap(self, mailboxes: list[str]) -> dict[str, int]:
        results = {}
        for mailbox in mailboxes:
            if mailbox.lower() != 'inbox':
                logger.warning("POP3 only supports INBOX, but %s requested", mailbox)
                # results[mailbox] = 0
                continue
            # mails = self.get_mails_list()
            # return len(mails)
            count, size = self.server.stat()
            results[mailbox] = count
        return results
    
    def get_mailboxes(self):
        return ["inbox"]

    def get_mail_by_index(self, index, mailbox="inbox"):
        if mailbox.lower() != 'inbox':
             logger.warning("POP3 only supports INBOX, but %s requested", mailbox)
        resp_status, mail_lines, mail_octets = self.server.retr(index)
        return Email(mail_lines)

    def refresh_connection(self):
        # pop3 cannot be polled using same connection (specified RFC)
        # keep querying will only return same results
        
        # self.server.noop()
        # self.server.user(self.email_account)
        # self.server.pass_(self.password)
        # self.server.rset()
        self.kill()
        self.server = self.connect()

    def cleanup(self):
        self.server.quit()
    
    def kill(self):
        self.server.close()

if __name__ == '__main__':
    testMain(EmailClientPOP3)