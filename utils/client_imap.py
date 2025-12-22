import os
import re
from typing import Dict, List, Tuple, Optional
from base64 import b64decode
import logging
import imaplib
import shlex
from urllib.parse import ParseResult, urlparse

from .client_base import EmailClientBase, testMain
from .oauth2_helper import OAuth2Factory, Token
from .mail import Email

logger = logging.getLogger(__name__)

_STATUS_KV_RE = re.compile(r"(MESSAGES|UNSEEN|RECENT|UIDNEXT|UIDVALIDITY)\s+(\d+)", re.I)

class EmailClientIMAP(EmailClientBase):
    def __init__(self, email_account, passwd, server_uri=None):
        self.email_account = email_account
        self.password = passwd
        if not server_uri:
            server_uri = 'imaps://imap.'+self.email_account.split('@')[-1]
        self.server_uri: ParseResult = urlparse(server_uri) # type: ignore
        self.server = self.connect()
        self.current_mailbox = None

    def connect(self):
        if self.server_uri.scheme == 'imaps':
            server = imaplib.IMAP4_SSL(self.server_uri.hostname, self.server_uri.port or imaplib.IMAP4_SSL_PORT)
        else:
            # TODO: implement imap starttls
            raise RuntimeError(f'Unsupported IMAP protocol variant: {self.server_uri.scheme}')

        if os.getenv('IMAPDEBUG'):
            server.debug = 100
        
        # display the welcome info received from server,
        # indicating the connection is set up properly
        logger.info('imap server welcome: %s', server.welcome.decode('utf8'))
        # authenticating
        token = OAuth2Factory.token_from_string(self.password)
        if token is None:
            # normal basic auth
            status, statusText = server.login(self.email_account, self.password)
        else:
            saslBody = token.getSasl(self.email_account)
            # status, statusText = server._simple_command('AUTHENTICATE', 'XOAUTH2', saslBody) # this will break imaplib's internal state
            # IMAP4.authenticate receives raw SASL bytes instead of base64 string, so we decode it first
            status, statusText = server.authenticate("XOAUTH2", lambda _: b64decode(saslBody))
        assert status == 'OK', f'imap failed to login: {status}'
        logger.info('imap login ok: %s', statusText)
        return server

    def _ensure_selected(self, mailbox, force=False):
        if not force and self.current_mailbox == mailbox:
            return None
        status, inboxdata = self.server.select(mailbox, readonly=True)
        assert status == 'OK', f'imap failed to select: {status}'
        self.current_mailbox = mailbox
        return inboxdata

    def _status_mailbox(self, mailboxes: List[str], items: Tuple[str, ...] = ("MESSAGES", "UNSEEN")) -> Dict[str, Dict[str, int]]:
        """
        Ask server for mailbox STATUS for all mailboxes using LIST-STATUS extension.
        Returns a dict like {"INBOX": {"MESSAGES": 123, "UNSEEN": 4}}
        """
        status_items = " ".join(items)
        # LIST "" "*" RETURN (STATUS (items))
        typ, data = self.server._simple_command(
            'LIST', '""', '*', 'RETURN', f'(STATUS ({status_items}))'
        )
        if typ != "OK":
            raise RuntimeError(f"LIST-STATUS failed: {typ} {data}")

        results = {}
        for line in data:
            if not line: continue
            if isinstance(line, bytes):
                s = line.decode("utf-8", errors="replace")
            else:
                s = str(line)
            
            # Parse response line: ... (STATUS (...))
            match = re.search(r'^(.*)\s+\(STATUS\s+\((.*)\)\)$', s)
            if match:
                mailbox_info = match.group(1)
                status_info = match.group(2)
                
                # Parse mailbox name using simplified logic from get_mailboxes
                m_box = re.match(r'^\(([^)]*)\)\s+\S+\s+(.*)$', mailbox_info)
                if m_box:
                    name_raw = m_box.group(2)
                    name_tokens = shlex.split(name_raw)
                    name = name_tokens[0] if name_tokens else name_raw
                else:
                    tokens = shlex.split(mailbox_info)
                    name = tokens[-1]
                
                # Parse status values
                st = {}
                for k, v in _STATUS_KV_RE.findall(status_info):
                    st[k.upper()] = int(v)
                
                results[name] = st
                
        return results

    def get_mails_countmap(self, mailboxes: List[str]) -> Dict[str, int]:
        results: Dict[str, int] = {}
        
        # Try batch STATUS first
        try:
            statuses = self._status_mailbox(mailboxes, items=("MESSAGES",))
            for mb in mailboxes:
                if mb in statuses:
                    results[mb] = statuses[mb].get('MESSAGES', 0)
                    logger.debug("Retrieved using LIST-STATUS: %d messages for %s", results[mb], mb)
        except Exception as e:
            logger.debug("LIST-STATUS command failed, falling back to SELECT loop: %s", e)
            
        for mb in mailboxes:
            if mb in results:
                continue
                
            try:
                # Fallback to SELECT
                # We force selection to get the latest count
                inboxdata = self._ensure_selected(mb, force=True)
                results[mb] = int(inboxdata[0].decode())
            except Exception as e2:
                logger.info("Failed to get count for %s, skipping: %s", mb, e2)
                results[mb] = 0
        return results
    
    def get_mailboxes(self):
        status, boxes = self.server.list()
        assert status == 'OK', f'imap failed to list mailboxes: {status}'
        names = []
        for box in boxes:
            # box is bytes
            s = box.decode()
            # simple parsing: name is the last part, usually quoted
            # e.g. (\HasNoChildren) "/" "INBOX"
            # e.g. (\HasChildren) "/" "My Folder"
            logger.debug("Got box: %s", box)
            import shlex
            try:
                # Regex to extract flags inside parentheses at the start
                # Pattern: (flags) separator name
                match = re.match(r'^\(([^)]*)\)\s+\S+\s+(.*)$', s)
                if match:
                    flags_str = match.group(1)
                    name_raw = match.group(2)
                    flags = set(flags_str.split())
                    
                    if '\\Noselect' in flags:
                        logger.debug("Skipping mailbox with \\Noselect: %s", s)
                        continue
                        
                    # Parse the name part, handling quotes
                    name_tokens = shlex.split(name_raw)
                    name = name_tokens[0] if name_tokens else name_raw
                else:
                    # Fallback to old simple parsing if regex doesn't match
                    tokens = shlex.split(s)
                    name = tokens[-1]
                # Specially handle INBOX, because IMAP RFC claims it's case-insensitive
                if name.lower() == 'inbox':
                    name = "inbox" # we use "inbox", becasue we also use it as stub name for POP3
                names.append(name)
            except Exception as e:
                logger.warning("failed to parse mailbox name: %s, error: %s", s, e)
        return names

    def get_mail_by_index(self, index, mailbox="inbox"):
        self._ensure_selected(mailbox)
        
        status, data = self.server.fetch('%d' % index, '(RFC822)')
        assert status == 'OK', f'imap failed to fetch: {status}'
        mail_lines = data[0][1]
        assert isinstance(mail_lines, bytes), f'imap fetch returned non-bytes: {type(mail_lines)}'
        return Email(mail_lines)

    def refresh_connection(self):
        self.server.noop()
    
    def cleanup(self):
        self.server.logout()
    
    def kill(self):
        self.server.close()

if __name__ == '__main__':
    # import sys
    # useraccount = sys.argv[1]
    # password = sys.argv[2]

    # client = EmailClientIMAP(useraccount, password)
    # num = client.get_mails_count()
    # print(num)
    # for i in range(1, num):
    #     print(client.get_mail_by_index(i))
    testMain(EmailClientIMAP)