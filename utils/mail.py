import re
from typing import List, Optional, Tuple, Union
from pyzmail import PyzMessage, decode_text # type: ignore
from pyzmail.parse import MailPart # type: ignore

import logging

from utils.telegram_helper import tg_md2_escape
logger = logging.getLogger(__name__)

def _cleanup_html(html: str) -> str:
    import bleach
    allowed_tags = [
        'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del',
        'tg-spoiler', 'a', 'tg-emoji', 'code', 'pre', 'br'
    ]
    allowed_attributes = {
        'a': ['href'],
        'code': ['class']
    }
    allowed_protocols = ['http', 'https', 'tg']
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    cleaned_html = bleach.clean(
        html,
        tags=allowed_tags,
        attributes=allowed_attributes,
        protocols=allowed_protocols,
        strip=True,
        strip_comments=True,
    )
    lines = cleaned_html.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    return "\n".join(non_empty_lines)

def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)

    # If scheme is missing (e.g. "example.com/path"), urlparse puts it in .path
    host = p.hostname
    if host is None:
        p = urlparse("http://" + url)
        host = p.hostname

    return host or ""

def _render_html(html: str) -> str:
    from markdownify import markdownify as md # type: ignore
    mdbody = md(_cleanup_html(html))
    logger.debug("mdbody: %s", mdbody)
    def replaceUrl(match: re.Match) -> str:
        name = match.group('name')
        url = match.group('url')
        host = extract_domain(url)
        name_better = f"{name}({host[:20]}...)"
        return f"[{name_better}]({url})"
    mdbody = re.sub(r'\[(?P<name>.*?)\]\((?P<url>http.*?)\)', replaceUrl, mdbody)
    logger.debug("cleaned mdbody: %s", mdbody)
    return mdbody

URL_REGEX = re.compile('''
    https?://                       # scheme: http:// or https://

    (                               # --- host (one or more labels + TLD) ---
        (?:[A-Za-z0-9-]+\.)+        # one or more DNS labels ending with dot, e.g. "www." / "a-b."
        [A-Za-z]{2,63}               # TLD: 2~6 letters (e.g. com, net, museum*)
    )

    (?:                             # --- optional port ---
        :                           # colon before port
        [0-9]{1,5}                  # port: 1~5 digits (0~65535 not range-checked here)
    )?

    (?:                             # --- optional path/query/fragment part ---
        /                           # leading slash for the rest
        [A-Za-z0-9\-._~:/?#[\]%@!$&'()*+,;=]*  # RFC3986-ish allowed chars (incl. %)
    )?
''', re.X)
def _cleanup_text(text: str) -> str:
    """
    This function tries to convert plaintext into markdown.
    Currently it is only converting URL into marklink link.
    """
    def _replace_url(url: re.Match[str]) -> str:
        domain = extract_domain(url.group(0))
        return f'[🔗{domain}...]({url.group(0)})'
    text = re.sub(URL_REGEX, _replace_url, text)
    return text

class Email(object):
    def __init__(self, raw_mail_lines: Union[bytes, List[bytes]]):
        assert isinstance(raw_mail_lines, (bytes, list)), "raw_mail_lines must be bytes or list of bytes"
        if isinstance(raw_mail_lines, bytes):
            self.msg_content = raw_mail_lines
        else:
            self.msg_content = b'\r\n'.join(raw_mail_lines)
        try:
            msg =  PyzMessage.factory(self.msg_content)

            self.subject = msg.get_subject()
            self.sender = msg.get_address('from')
            self.date = msg.get_decoded_header('date', '')
            self.id = msg.get_decoded_header('message-id', '')
            # self.headers = {k: msg.get_decoded_header(k, '') for k in msg.keys()}

            self.text = None
            self.html = None
            self.additional_parts = []
            for mailpart in msg.mailparts:
                mailpart: MailPart
                is_body = mailpart.is_body or ''
                if is_body.startswith('text/html'):
                    payload, used_charset=decode_text(mailpart.get_payload(), mailpart.charset, None)
                    self.html = payload
                elif is_body.startswith('text/') or (
                    not is_body and not mailpart.type): # strange email with none mime
                    payload, used_charset=decode_text(mailpart.get_payload(), mailpart.charset, None)
                    self.text = payload
                else:
                    self.additional_parts.append(mailpart)
        except Exception as e:
            raise Exception("Cannot parse email body: %s" % self.msg_content) from e

    def __repr__(self):
        text, _ = self.format_email()
        return text

    def format_email(self, prefer_html=False):
        mail_str = "Subject: %s\n" % self.subject
        mail_str += "From: %s <%s>\n" % self.sender
        mail_str += "Date: %s\n" % self.date
        mail_str += "ID: %s\n" % self.id
        mail_str += "\n"
        mainbody = _cleanup_text(self.text or "")
        retfiles: List[Tuple[Optional[str], Optional[str], Optional[bytes]]] = []
        isStrangeText = not self.text or len(self.text) < 20
        if prefer_html or isStrangeText: # not like a real email or we prefer html
            logger.debug("Email body text very strange, trying to use html: %s", self.text)
            if self.html:
                logger.debug("Using html as html body...")
                rawbody = self.html
                try:
                    logger.debug("Rendering html body...")
                    mainbody = _render_html(rawbody)
                    logger.debug("Rendered html body: %s", mainbody)
                except Exception as e:
                    logger.warning("Failed to render html body: %s, falling back to send raw html", e) 
                    mainbody = "*(body not available, see raw html in file)*"
                # retfiles.append((f"emailbot-full-{self.id}.html", "text/plain", rawbody.encode()))
            else:
                logger.debug("email body html invalid: %s, keep using text body instead", self.html)
        else:
            logger.debug("Directly using text body: %s", self.text)
        if self.html:
            # If there's html, always enclose a raw copy just in case there's any error
            retfiles.append((f"emailbot-full-{self.id}.html", "text/plain", self.html.encode()))
        if self.additional_parts:
            mainbody += f'\n\nAdditional Parts:'
            for part in self.additional_parts:
                part: MailPart
                part_name = part.filename
                part_content = part.get_payload()
                mainbody += f'\n- {part_name} ({part.type}, size {len(part_content)})'
                retfiles.append((part_name, part.type, part_content))
        mail_str += mainbody
        return mail_str, retfiles