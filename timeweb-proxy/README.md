# Slovo Timeweb proxy

Stateless HTTPS entry point for the Slovo Telegram Mini App. Timeweb App
Platform terminates HTTPS and this Nginx container proxies requests through the
private network to `http://192.168.0.10:8000`.

The App Platform application must be attached to the same Timeweb private
network as the Slovo VM.
