import os
from dotenv import dotenv_values
from typing import get_type_hints

os.environ.update({k:v for k,v in dotenv_values().items() if v})

def getconf(x):
    return os.environ.get(x) # or dotenv_values().get(x)

class Conf:
    TELEGRAM_TOKEN: str
    OWNER_CHAT_ID: int
    POLL_INTERVAL: int = 60
    ERR_REPORT_INTERVAL: int = 3600
    REQUEST_TIMEOUT: int = 60
    POLL_THREADS: int = 5
    
    SAVE_EMAIL_LOGS: bool = False
    ENABLED_PLUGINS: str = ''
    
    _defaultValues: dict | None = None
    @classmethod
    def _reloadConf(cls):
        if cls._defaultValues is None:
            cls._defaultValues = dict(Conf.__dict__)
        
        def hasDefaultValue(x):
            return cls._defaultValues[field] is not None
        for field, typ in get_type_hints(cls).items():
            if field.startswith('_'):
                continue
            val = getconf(field)
            if val is None:
                if hasDefaultValue(field):
                    continue
                raise Exception('%s not set in env or .env file' % field)
            finalVal = val
            if typ is str:
                finalVal = val
            elif typ is int:
                try:
                    finalVal = int(val)
                except Exception:
                    raise Exception('The value of %s cannot be converted to int (value=%s)' % (field, val))
            elif typ is bool:
                if val.lower() == 'true' or val.lower() == 'yes':
                    finalVal = True
                elif val.lower() == 'false' or val.lower() == 'no':
                    finalVal = False
                else:
                    raise Exception('The value of %s cannot be converted to bool (value=%s), use YES/NO or TRUE/FALSE to represent boolean!' % (field, val))
            else:
                raise Exception("Invalid config var type %s" % typ)
            setattr(cls, field, finalVal)

Conf._reloadConf()