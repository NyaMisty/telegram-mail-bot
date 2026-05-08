def get_mail_server(email_addr):
    _, _, domain = email_addr.partition('@')
    match domain.lower():
        case "gmail.com":
            return 'imaps://imap.gmail.com', 'smtps://smtp.gmail.com'
        case "hotmail.com" | "outlook.com" | "live.com":
            return 'imaps://outlook.office365.com', 'smtp+starttls://smtp-mail.outlook.com'
        case "yandex.ru" | "list.ru" | "ya.ru":
            return "imaps://imap.yandex.com", "smtps://smtp.yandex.com"
        case "rambler.ru":
            return 'imaps://imap.rambler.ru', 'smtps://imap.rambler.ru'
        case "gmx.com":
            return 'imaps://imap.gmx.com', 'smtps://mail.gmx.com'
        case "protonmail.com" | "pm.me" | "proton.me" | "protonmail.ch":
            return 'proton://', None
    
    return None