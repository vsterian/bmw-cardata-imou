# Security

Never commit `.env`, BMW tokens, Imou credentials, SSH credentials, VINs, or
location coordinates. Keep runtime secrets on Raspberry Pi and deployment
credentials in GitHub Actions secrets.

If a credential is exposed, revoke or rotate it immediately and replace it in
its runtime secret store.
