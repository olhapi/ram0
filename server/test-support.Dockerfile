FROM python:3.12-alpine

WORKDIR /app

COPY test_support/openai_stub.py ./openai_stub.py

RUN addgroup -S stub && adduser -S -G stub stub

USER stub

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "openai_stub.py"]
