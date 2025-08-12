from typing import List, Optional, Tuple, Union
from pyzmail import PyzMessage, decode_text # type: ignore
from pyzmail.parse import MailPart # type: ignore
import html

import logging
logger = logging.getLogger(__name__)

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
            self.text = None
            self.html = None
            self.html_raw = None
            self.additional_parts = []
            for mailpart in msg.mailparts:
                mailpart: MailPart
                is_body = mailpart.is_body or ''
                if is_body.startswith('text/html'):
                    payload, used_charset = decode_text(mailpart.get_payload(), mailpart.charset, None)
                    self.html_raw = payload
                    try:
                        from markdownify import markdownify as md # type: ignore
                        self.html = md(payload)
                    except Exception:
                        logger.warning("cannot use markdownify to convert html, fallback to raw HTML instead.")
                        self.html = payload
                elif is_body.startswith('text/') or (
                    not is_body and not mailpart.type): # strange email with none mime
                    payload, used_charset = decode_text(mailpart.get_payload(), mailpart.charset, None)
                    self.text = payload
                else:
                    self.additional_parts.append(mailpart)
        except Exception as e:
            raise Exception("Cannot parse email body: %s" % self.msg_content) from e

    def __repr__(self):
        text, _ = self.format_email()
        return text

    def format_email(self):
        mail_str = "Subject: %s\n" % self.subject
        mail_str += "From: %s %s\n" % self.sender
        mail_str += "Date: %s\n" % self.date
        mail_str += "ID: %s\n" % self.id
        mail_str += "\n"
        mainbody = self.text
        if not self.text or len(self.text) < 20: # not like a real email
            mainbody = self.html or self.text or ''
        retfiles: List[Tuple[Optional[str], Optional[str], Optional[bytes]]] = []

        # Normalize body to str
        if mainbody is None:
            mainbody = ''
        elif isinstance(mainbody, bytes):
            try:
                mainbody = mainbody.decode('utf-8', errors='replace')
            except Exception:
                mainbody = str(mainbody)
        # Long body: move to .htm attachment, keep short preview in message (only if threshold > 0)
        from utils.conf import Conf
        threshold = Conf.LONG_BODY_TO_HTML_THRESHOLD

        if threshold > 0 and isinstance(mainbody, str) and len(mainbody) > threshold:
            if self.html_raw:
                html_payload = self.html_raw if isinstance(self.html_raw, str) else str(self.html_raw)
            else:
                safe_text = html.escape(mainbody).replace("\n", "<br/>\n")
                html_payload = (
                    "<html><head><meta charset='utf-8'></head>"
                    "<body><pre style='white-space:pre-wrap'>" + safe_text + "</pre></body></html>"
                )
            # Insert body.htm as the first attachment
            retfiles.insert(0, ("body.html", "text/html", html_payload.encode("utf-8")))
            mainbody = mainbody[:threshold] + "..."
        if self.additional_parts:
            mainbody += '\n\nAdditional Parts:'
            for part in self.additional_parts:
                part: MailPart
                part_name = part.filename
                part_content = part.get_payload()
                mainbody += f'\n- {part_name} ({part.type}, size {len(part_content)})'
                retfiles.append((part_name, part.type, part_content))
        mail_str += mainbody
        return mail_str, retfiles