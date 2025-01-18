from typing import List, Optional, Tuple
from pyzmail import PyzMessage, decode_text # type: ignore
from pyzmail.parse import MailPart # type: ignore

import logging
logger = logging.getLogger(__name__)

class Email(object):
    def __init__(self, raw_mail_lines):
        if isinstance(raw_mail_lines, str):
            msg_content = raw_mail_lines
        else:
            msg_content = b'\r\n'.join(raw_mail_lines)
        try:
            msg =  PyzMessage.factory(msg_content)

            self.subject = msg.get_subject()
            self.sender = msg.get_address('from')
            self.date = msg.get_decoded_header('date', '')
            self.id = msg.get_decoded_header('message-id', '')

            self.text = None
            self.html = None
            self.additional_parts = []
            for mailpart in msg.mailparts:
                mailpart: MailPart
                is_body = mailpart.is_body or ''
                if is_body.startswith('text/html'):
                    payload, used_charset=decode_text(mailpart.get_payload(), mailpart.charset, None)
                    try:
                        from markdownify import markdownify as md # type: ignore
                        self.html = md(payload)
                    except Exception:
                        logger.warning("cannot use markdownify to convert html, fallback to raw HTML instead.")
                        self.html = payload
                elif not is_body or is_body.startswith('text/'):
                    payload, used_charset=decode_text(mailpart.get_payload(), mailpart.charset, None)
                    self.text = payload
                else:
                    self.additional_parts.append(mailpart)
        except Exception as e:
            raise Exception("Cannot parse email body: %s" % raw_mail_lines) from e

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
        if self.additional_parts:
            mainbody += f'\n\nAdditional Parts:\n'
            for part in self.additional_parts:
                part: MailPart
                part_name = part.filename
                part_content = part.get_payload()
                mainbody += f'- {part_name} ({part.type}, size {len(part_content)})'
                retfiles.append((part_name, part.type, part_content))
        mail_str += mainbody
        return mail_str, retfiles