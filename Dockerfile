FROM docker.io/oz123/pipenv:3.11-v2023-6-26 AS builder

# Tell pipenv to create venv in the current directory
ENV PIPENV_VENV_IN_PROJECT=1

ADD Pipfile.lock Pipfile /workdir/

WORKDIR /workdir

RUN /usr/local/bin/pipenv sync

######### Build Stage Finished ##########

FROM python:3.11-alpine AS runtime

RUN mkdir -pv /workdir/.venv

COPY --from=builder /workdir/.venv/ /workdir/.venv/

WORKDIR /workdir/
VOLUME /workdir/conf

ADD . /workdir/src

CMD ["./.venv/bin/python", "src/bot.py"]
