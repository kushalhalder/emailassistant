# Usage Instructions

- Create the `.env` file in the root directory with the following contents:
```
AZURE_TENANT_ID
AZURE_CLIENT_ID
AZURE_CLIENT_SECRET
USER_EMAIL
OPENAI_API_KEY
```

- Create the `config.yaml` file in the root directory. Customise the template from config.yaml.tpl
```sh
cp config.yaml.tpl config.yaml
```

- Run the script with:
Use python 3.12 or higher.
```sh
pip install -r requirements.txt
python main.py
```
## Commands to use in the TUI
- `start` - Start the email processing
- `stop` - Stop the email processing
- `restart` - Restart the email processing (if the email processing is stopped)
- `status` - Show the status of the email processing
- `help` - Show the help message
- `exit` - Exit the TUI (if you want to stop the email processing)


# Next features
- Add options to add other LLM Providers
- Add option to draft a response to the email
- Add option 
