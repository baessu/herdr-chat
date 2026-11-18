# Notes for coding agents

- **Asked to install or set up herdr-chat?** Follow [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md).
- **Changing the code?**
  - Read [API.md](API.md) first; it is the contract between `server.py` and `web/`. Change it
    together with the code.
  - Keep the server on the Python standard library (3.9+). `cryptography` is the only optional
    dependency, for push.
  - Run `python3 -m unittest discover -s tests`. Tests use `tests/fake_hc.py`.
  - Never test by sending input to terminals you did not start. Start your own with
    `hc run --bg --name test-… <program>`, and `hc kill` / `hc rm` it when done.
  - Open agent history files and databases read-only.
  - Comments in this codebase are mostly Korean; match the surrounding style.
