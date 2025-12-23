import logging
from typing import Type

from .oauth2_helper import OAuth2Factory

from .mail import Email

logger = logging.getLogger(__name__)

class EmailClientBase(object):
    def __init__(self, email_account, passwd, server_uri=None):
        raise NotImplementedError()
    
    @staticmethod
    def connect(self):
        raise NotImplementedError()
    
    def get_mails_countmap(self, mailboxes: list[str]) -> dict[str, int]:
        """
        Get the count of emails in each mailbox.

        Args:
            mailboxes (list[str]): A list of mailbox names.

        Returns:
            dict[str, int]: A dictionary mapping mailbox names to email counts. 
                            Only succeeded mailboxes will be included.
        """
        raise NotImplementedError()
    
    def get_mailboxes(self) -> list[str]:
        return ["inbox"]
    
    def get_mail_by_index(self, index, mailbox="inbox") -> Email:
        raise NotImplementedError()
    
    def refresh_connection(self) -> None:
        raise NotImplementedError()
    
    def cleanup(self) -> None:
        raise NotImplementedError()
    
    def kill(self) -> None:
        raise NotImplementedError()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False
    
    # def __enter__(self):
    #     return self

    # def __exit__(self, exc_type, exc_val, exc_tb):
    #     if exc_type is None:
    #         logger.info('exited normally\n')
    #         self.cleanup()
    #     else:
    #         logger.error('raise an exception! ' + str(exc_type))
    #         self.server.close()
    #         return False # Propagate

def testMain(EmailClient: Type[EmailClientBase]):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=logging.DEBUG)

    import sys
    import time
    
    useraccount = sys.argv[1]
    password = sys.argv[2]
    server_uri = sys.argv[3]
    
    new_passwd = OAuth2Factory.code_to_token(password)
    if new_passwd:
        print('changed code into token', new_passwd)
        password = new_passwd

    client = EmailClient(useraccount, password, server_uri)
    countmap = client.get_mails_countmap(['inbox'])
    num = countmap.get('inbox', 0)
    print(client.get_mail_by_index(num))
    print(num)
    if True:
        def ipy_here():
            from IPython import embed
            import inspect
            f = inspect.currentframe().f_back
            ns = f.f_globals.copy()
            ns.update(f.f_locals)          # 拷贝一份，别把 f_locals 当 live locals
            embed(user_ns=ns)

        ipy_here()
    elif False:
        for i in range(1, num):
            print(client.get_mail_by_index(i))
    elif True:
        inbox_num = num
        while True:
            client.refresh_connection()
            new_inbox_num = client.get_mails_countmap(['inbox']).get('inbox', 0)
            # print(new_inbox_num, len(client.server.uidl()))
            if new_inbox_num > inbox_num:
                for idx in range(inbox_num + 1, new_inbox_num + 1):
                    try:
                        mail = client.get_mail_by_index(idx)
                    except Exception:
                        logger.warning('cannot retrieve mail %d', idx, exc_info=True)
                        continue
                    
                    print('Got new mail:', mail)
                    inbox_num = new_inbox_num
            time.sleep(5)
    elif True:
        print(client.get_mail_by_index(27))