# telegram-mail-bot

A Telegram bot that retrives the newest email periodically and sends them to you as chat messages.


![Python Version](https://img.shields.io/badge/python-3.6-blue.svg)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Usage

### Setup
```
/add_email john.doe@example.com password protocol://server[:port] smtp_protocol://server[:port]
/list_email
/del_email john.doe@example.com
/help get help
```

Example:
```
/add_email john.doe@hotmail.com password pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
/add_email john.doe@gmail.com password imaps://imap.gmail.com:993 smtps://smtp.gmail.com
```

Example for OAuth:
- MS Personal Account: Login at https://login.microsoftonline.com/common/oauth2/v2.0/authorize?response_type=code&client_id=55797b5d-1e14-44bc-a7b3-52575eb1d6ef&redirect_uri=https%3A%2F%2Flocalhost&scope=https%3A%2F%2Foutlook.office.com%2FIMAP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FPOP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FSMTP.Send+offline_access
    - Using authorization code:
        ```
        /add_email john.doe@hotmail.com code:ms:M.XXXXXXXX pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
        ```
    - Using refresh token:
        ```
        /add_email john.doe@hotmail.com token:ms:XXXXXXXX pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
        ```
    - Using refresh token with custom client_id (beware there's 3 ":" before client_id):
        ```
        /add_email john.doe@hotmail.com token:ms:XXXXXXXX:::XXXXXXXXXXXX-XXXX-XXXX-... pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
        ```
- MS Organization Account: Login using same URL as above
    - Using authorization code:
        ```
        /add_email john.doe@XXXX.onmicrosoft.com code:ms-org:XXXXXXXX pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
        ```
    - Using refresh token:
        ```
        /add_email john.doe@XXXX.onmicrosoft.com token:ms-org:XXXXXXXX pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
        ```

After setup, you can then reply on received email to send reply with SMTP


## Deploy

### 1. Configure .env

Copy .env.template to .env, and modify it on your own

### 2. Start the server

#### Docker-Compose (recommended)

```
docker-compose up -d
```

- Note: use below command when updating
```
docker-compose up -d --build
```

#### Run Directly

```
./run.sh
```