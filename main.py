import asyncio
from msgraph import GraphServiceClient
from azure.identity import ClientSecretCredential
from instructor import from_litellm
from litellm import completion
import litellm
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
from msgraph.generated.models.message import Message
from pydantic import BaseModel, Field
from typing import Literal
import os
from dotenv import load_dotenv
import yaml
from datetime import datetime
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Log, Input, Static
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel

load_dotenv()

class EmailClassification(BaseModel):
    category: Literal["spam", "newsletter", "notify", "urgent", "normal"] = Field(
        description="Email category based on triage rules"
    )
    priority: int = Field(
        ge=1, le=10, 
        description="Priority score from 1-10 (1=lowest, 10=highest)"
    )
    should_notify: bool = Field(
        description="Whether to send notification to user"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score from 0.0-1.0"
    )
    reasoning: str = Field(
        description="Brief explanation of the classification decision"
    )

class EmailAssistant:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, user_email: str, config_path: str = "config.yaml"):
        # Load configuration
        with open(config_path, 'r') as file:
            self.config = yaml.safe_load(file)
        
        # Initialize Microsoft Graph client
        self.credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        self.graph_client = GraphServiceClient(
            credentials=self.credential,
            scopes=['https://graph.microsoft.com/.default']
        )
        
        # Store the user whose emails to process
        self.user_email = user_email
        
        # Initialize LLM provider configuration
        self.llm_config = self.config.get('llm', {})
        self.provider = self.llm_config.get('provider', 'openai')
        
        # Configure LiteLLM based on provider
        self._configure_litellm()
        
        # Validate provider configuration
        self._validate_provider_config()
        
        # Initialize LLM with structured outputs  
        self.llm_client = from_litellm(completion)
        
        # State management
        self.is_running = False
        self.is_paused = False
        self.marked_as_read_logger = None
        self.needs_reply_logger = None
        self.general_logger = None
    
    def _configure_litellm(self):
        """Configure LiteLLM based on the selected provider"""
        provider_config = self.llm_config.get(self.provider, {})
        
        if self.provider == 'openai':
            # OpenAI configuration
            litellm.api_key = os.getenv("OPENAI_API_KEY")
            if os.getenv("OPENAI_BASE_URL"):
                litellm.api_base = os.getenv("OPENAI_BASE_URL")
            if os.getenv("OPENAI_ORGANIZATION"):
                litellm.organization = os.getenv("OPENAI_ORGANIZATION")
                
        elif self.provider == 'anthropic':
            # Anthropic configuration
            litellm.api_key = os.getenv("ANTHROPIC_API_KEY")
            
        elif self.provider == 'gemini':
            # Google Gemini configuration
            litellm.api_key = os.getenv("GEMINI_API_KEY")
            
        elif self.provider == 'azure':
            # Azure OpenAI configuration
            litellm.api_key = os.getenv("AZURE_API_KEY")
            litellm.api_base = os.getenv("AZURE_API_BASE")
            litellm.api_version = os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
            
        elif self.provider == 'ollama':
            # Ollama configuration
            base_url = provider_config.get('base_url', 'http://localhost:11434')
            litellm.api_base = base_url
            
        # Get model from provider-specific config
        self.model = provider_config.get('model', 'gpt-4o-mini')
        
        # Store provider config for use in API calls
        self.provider_config = provider_config
    
    def _get_model_name(self):
        """Get the properly formatted model name for the selected provider"""
        if self.provider == 'azure':
            # For Azure, use deployment name if provided, otherwise use model name
            deployment_name = self.provider_config.get('deployment_name')
            if deployment_name:
                return f"azure/{deployment_name}"
            else:
                return f"azure/{self.model}"
        elif self.provider == 'anthropic':
            # Anthropic models need the anthropic/ prefix
            return f"anthropic/{self.model}"
        elif self.provider == 'gemini':
            # Gemini models need the gemini/ prefix
            return f"gemini/{self.model}"
        elif self.provider == 'ollama':
            # Ollama models need the ollama/ prefix
            return f"ollama/{self.model}"
        else:
            # OpenAI models can use the model name directly
            return self.model
    
    def _validate_provider_config(self):
        """Validate that required environment variables are set for the selected provider"""
        missing_vars = []
        
        if self.provider == 'openai':
            if not os.getenv("OPENAI_API_KEY"):
                missing_vars.append("OPENAI_API_KEY")
                
        elif self.provider == 'anthropic':
            if not os.getenv("ANTHROPIC_API_KEY"):
                missing_vars.append("ANTHROPIC_API_KEY")
                
        elif self.provider == 'gemini':
            if not os.getenv("GEMINI_API_KEY"):
                missing_vars.append("GEMINI_API_KEY")
                
        elif self.provider == 'azure':
            if not os.getenv("AZURE_API_KEY"):
                missing_vars.append("AZURE_API_KEY")
            if not os.getenv("AZURE_API_BASE"):
                missing_vars.append("AZURE_API_BASE")
                
        elif self.provider == 'ollama':
            # Ollama doesn't require API keys, but we could validate base_url accessibility
            pass
        
        if missing_vars:
            raise ValueError(
                f"Missing required environment variables for {self.provider} provider: {', '.join(missing_vars)}"
            )
    
    def set_loggers(self, marked_as_read_logger, needs_reply_logger, general_logger):
        """Set the loggers for outputting messages to different columns"""
        self.marked_as_read_logger = marked_as_read_logger
        self.needs_reply_logger = needs_reply_logger
        self.general_logger = general_logger
    
    def log_to_marked_as_read(self, message: str, level: str = "INFO"):
        """Log to the marked as read column"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self.marked_as_read_logger:
            if level == "ERROR":
                self.marked_as_read_logger.write_line(f"[red][{timestamp}] {message}[/red]")
            elif level == "SUCCESS":
                self.marked_as_read_logger.write_line(f"[green][{timestamp}] {message}[/green]")
            elif level == "WARNING":
                self.marked_as_read_logger.write_line(f"[yellow][{timestamp}] {message}[/yellow]")
            else:
                self.marked_as_read_logger.write_line(f"[{timestamp}] {message}")
    
    def log_to_needs_reply(self, message: str, level: str = "INFO"):
        """Log to the needs reply column"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self.needs_reply_logger:
            if level == "ERROR":
                self.needs_reply_logger.write_line(f"[red][{timestamp}] {message}[/red]")
            elif level == "SUCCESS":
                self.needs_reply_logger.write_line(f"[green][{timestamp}] {message}[/green]")
            elif level == "WARNING":
                self.needs_reply_logger.write_line(f"[yellow][{timestamp}] {message}[/yellow]")
            else:
                self.needs_reply_logger.write_line(f"[{timestamp}] {message}")
    
    def log(self, message: str, level: str = "INFO"):
        """Log a general message with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self.general_logger:
            if level == "ERROR":
                self.general_logger.write_line(f"[red][{timestamp}] ERROR: {message}[/red]")
            elif level == "SUCCESS":
                self.general_logger.write_line(f"[green][{timestamp}] SUCCESS: {message}[/green]")
            elif level == "WARNING":
                self.general_logger.write_line(f"[yellow][{timestamp}] WARNING: {message}[/yellow]")
            else:
                self.general_logger.write_line(f"[{timestamp}] {message}")
        else:
            print(f"[{timestamp}] {message}")
    
    async def process_inbox(self):
        """Main processing loop"""
        if not self.is_running or self.is_paused:
            return
            
        try:
            self.log("🔍 Checking inbox for new emails...")
            
            request_configuration = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
                query_parameters=MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
                    filter="isRead eq false",
                    top=50
                )
            )
            messages = await self.graph_client.users.by_user_id(self.user_email).messages.get(request_configuration=request_configuration)
            
            if not messages.value:
                self.log("📭 No unread emails found")
                return
            
            self.log(f"📧 Found {len(messages.value)} unread emails")
            
            for message in messages.value:
                if not self.is_running or self.is_paused:
                    break
                    
                classification = await self.classify_email(message)
                await self.handle_classification(message, classification)
                
        except Exception as e:
            self.log(f"Error processing inbox: {str(e)}", "ERROR")
    
    async def classify_email(self, message) -> EmailClassification:
        """Classify email using LLM"""
        try:
            content = f"Subject: {message.subject}\nFrom: {message.sender.email_address.address}\nBody: {message.body.content[:1000] if message.body.content else ''}"
            
            # self.log(f"🤖 Classifying: {message.subject[:50]}...")
            
            # Get system prompt from config
            system_prompt = self.config.get('system_prompt', '')
            
            # Prepare model name based on provider
            model_name = self._get_model_name()
            
            # Prepare API call parameters
            api_params = {
                'model': model_name,
                'response_model': EmailClassification,
                'messages': [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": f"Classify this email:\n\nSubject: {message.subject}\nFrom: {message.sender.email_address.address}\nSender Name: {getattr(message.sender.email_address, 'name', 'Unknown')}\nBody: {content}"
                    }
                ]
            }
            
            # Add optional parameters if configured
            if 'temperature' in self.provider_config:
                api_params['temperature'] = self.provider_config['temperature']
            if 'max_tokens' in self.provider_config:
                api_params['max_tokens'] = self.provider_config['max_tokens']
            if 'timeout' in self.provider_config:
                api_params['timeout'] = self.provider_config['timeout']
                
            # Add provider-specific parameters
            if self.provider == 'azure' and 'deployment_name' in self.provider_config:
                # For Azure, we might need to adjust the model name
                pass  # LiteLLM handles Azure deployment names automatically

            return self.llm_client.chat.completions.create(**api_params)
        except Exception as e:
            self.log(f"Error classifying email: {str(e)}", "ERROR")
            # Return default classification
            return EmailClassification(
                category="normal",
                priority=5,
                should_notify=False,
                confidence=0.0,
                reasoning=f"Error during classification: {str(e)}"
            )
    
    async def handle_classification(self, message, classification: EmailClassification):
        """Handle email based on classification"""
        try:
            from_addr = message.sender.email_address.address
            subject = message.subject[:50] + "..." if len(message.subject) > 50 else message.subject
            
            # Log to general area first
            self.log(f"📋 Processing: {subject}")
            
            if not classification.should_notify:
                # Auto-process emails that don't need notification
                await self.mark_as_read(message, classification)
            else:
                # These emails need attention/reply
                await self.send_notification(message, classification)
                
        except Exception as e:
            self.log(f"Error handling classification: {str(e)}", "ERROR")
    
    async def mark_as_read(self, message: Message, classification: EmailClassification):
        """Mark email as read and log to marked as read column"""
        try:
            # Create a proper Message object for the patch operation
            message_update = Message()
            message_update.is_read = True
            
            await self.graph_client.users.by_user_id(self.user_email).messages.by_message_id(message.id).patch(message_update)
            
            from_addr = message.sender.email_address.address
            subject = message.subject[:50] + "..." if len(message.subject) > 50 else message.subject
            
            self.log_to_marked_as_read(f"📧 {subject}")
            self.log_to_marked_as_read(f"👤 From: {from_addr} | Category: {classification.category} | Priority: {classification.priority}")
            self.log_to_marked_as_read(f"💭 {classification.reasoning}")
            # self.log_to_marked_as_read("✅ Marked as read", "SUCCESS")
            self.log_to_marked_as_read("---")
            
        except Exception as e:
            self.log(f"Error marking message as read: {str(e)}", "ERROR")
    
    async def send_notification(self, message, classification):
        """Send notification for urgent emails and log to needs reply column"""
        try:
            from_addr = message.sender.email_address.address
            subject = message.subject[:50] + "..." if len(message.subject) > 50 else message.subject
        
            self.log_to_needs_reply(f"📧 {subject}")
            self.log_to_needs_reply(f"👤 From: {from_addr} | Category: {classification.category} | Priority: {classification.priority}")
            self.log_to_needs_reply(f"💭 {classification.reasoning}")
            # self.log_to_needs_reply("🔔 URGENT - Needs immediate attention!", "WARNING")
            self.log_to_needs_reply("---")
        except Exception as e:
            self.log(f"Error sending notification: {str(e)}", "ERROR")
    
    def start(self):
        """Start the email processing"""
        self.is_running = True
        self.is_paused = False
        self.log("🚀 Email assistant started", "SUCCESS")
    
    def pause(self):
        """Pause the email processing"""
        self.is_paused = True
        self.log("⏸️ Email assistant paused", "WARNING")
    
    def resume(self):
        """Resume the email processing"""
        if self.is_running:
            self.is_paused = False
            self.log("▶️ Email assistant resumed", "SUCCESS")
        else:
            self.log("❌ Cannot resume - assistant not started yet", "ERROR")
    
    def stop(self):
        """Stop the email processing"""
        self.is_running = False
        self.is_paused = False
        self.log("🛑 Email assistant stopped", "WARNING")

class EmailAssistantApp(App):
    """Textual app for the Email Assistant"""
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+r", "restart", "Restart"),
    ]
    
    def __init__(self):
        super().__init__()
        self.assistant = None
        self.processing_task = None
        # Load CSS from external file
        self.CSS_PATH = "styles.css"
        try:
            with open(self.CSS_PATH, 'r') as f:
                self.CSS = f.read()
        except FileNotFoundError:
            self.CSS = ""  # Fallback to empty CSS if file not found
        
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        
        with Vertical():
            # Status display
            with Horizontal(classes="status-container"):
                yield Static("Status: [red]Stopped[/red]", id="status")
            
            # Two-column layout
            with Horizontal(classes="column-container"):
                # Left column - Marked as Read
                with Vertical(classes="left-column"):
                    yield Static("📫 Marked as Read (Auto-processed)", classes="column-title")
                    yield Log(id="marked_as_read_log", auto_scroll=True, max_lines=50)
                
                # Right column - Needs Reply  
                with Vertical(classes="right-column"):
                    yield Static("💬 Needs Reply (Requires Attention)", classes="column-title")
                    yield Log(id="needs_reply_log", auto_scroll=True, max_lines=50)
            
            # General log area (smaller, for system messages)
            with Vertical(classes="log-container"):
                yield Static("🔧 System Log", classes="column-title")
                yield Log(id="general_log", auto_scroll=True, max_lines=50)
            
            # Input area
            with Horizontal(classes="input-container"):
                yield Input(
                    placeholder="Enter command (start, resume, pause, exit)", 
                    id="command_input", 
                    classes="input-widget"
                )
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Called when app starts."""
        # Initialize the email assistant
        try:
            self.assistant = EmailAssistant(
                tenant_id=os.getenv("AZURE_TENANT_ID"),
                client_id=os.getenv("AZURE_CLIENT_ID"), 
                client_secret=os.getenv("AZURE_CLIENT_SECRET"),
                user_email=os.getenv("USER_EMAIL") 
            )
            self.assistant.set_loggers(
                marked_as_read_logger=self.query_one("#marked_as_read_log", Log),
                needs_reply_logger=self.query_one("#needs_reply_log", Log),
                general_logger=self.query_one("#general_log", Log)
            )
            self.assistant.log(f"🎉 Email Assistant initialized successfully with {self.assistant.provider} provider!", "SUCCESS")
            self.assistant.log(f"🤖 Using model: {self.assistant.model}")
            self.assistant.log("💡 Available commands: start, resume, pause, exit")
            
            # Add helpful instructions to each column
            self.assistant.log_to_marked_as_read("🤖 Emails automatically marked as read will appear here", "INFO")
            self.assistant.log_to_needs_reply("⚠️ Emails requiring your attention will appear here", "INFO")
            
        except ValueError as e:
            # Handle provider configuration errors specifically
            general_log = self.query_one("#general_log", Log)
            general_log.write_line(f"[red]❌ LLM Provider Configuration Error: {str(e)}[/red]")
            general_log.write_line("[yellow]💡 Please check your .env file and config.yaml for the selected provider[/yellow]")
        except Exception as e:
            general_log = self.query_one("#general_log", Log)
            general_log.write_line(f"[red]❌ Failed to initialize Email Assistant: {str(e)}[/red]")
        
        # Focus the input
        self.query_one("#command_input", Input).focus()
    
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command input."""
        command = event.value.strip().lower()
        input_widget = self.query_one("#command_input", Input)
        status_widget = self.query_one("#status", Static)
        
        if command == "start":
            if not self.assistant.is_running:
                self.assistant.start()
                status_widget.update("Status: [green]Running[/green]")
                # Start the processing loop
                self.processing_task = asyncio.create_task(self._processing_loop())
            else:
                self.assistant.log("⚠️ Assistant is already running", "WARNING")
                
        elif command == "resume":
            self.assistant.resume()
            if self.assistant.is_running and not self.assistant.is_paused:
                status_widget.update("Status: [green]Running[/green]")
            
        elif command == "pause":
            self.assistant.pause()
            status_widget.update("Status: [yellow]Paused[/yellow]")
            
        elif command == "exit":
            self.assistant.stop()
            status_widget.update("Status: [red]Stopped[/red]")
            if self.processing_task:
                self.processing_task.cancel()
            await asyncio.sleep(0.5)  # Give time for final log messages
            self.exit()
            
        elif command:
            self.assistant.log(f"❌ Unknown command: {command}", "ERROR")
            self.assistant.log("💡 Available commands: start, resume, pause, exit")
        
        # Clear the input
        input_widget.value = ""
    
    async def _processing_loop(self):
        """Main processing loop that runs in the background"""
        try:
            while self.assistant.is_running:
                if not self.assistant.is_paused:
                    await self.assistant.process_inbox()
                self.assistant.log("😴 Sleeping for 60 seconds")
                await asyncio.sleep(60)  # Check every minute
        except asyncio.CancelledError:
            self.assistant.log("🛑 Processing loop stopped")
        except Exception as e:
            self.assistant.log(f"💥 Error in processing loop: {str(e)}", "ERROR")

# Usage
async def main():
    app = EmailAssistantApp()
    await app.run_async()

if __name__ == "__main__":
    asyncio.run(main())