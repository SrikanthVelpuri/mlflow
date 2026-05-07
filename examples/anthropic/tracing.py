import os
import anthropic
import mlflow
# from dotenv import load_dotenv

def setup_mlflow_tracking():
    """Initialize MLflow auto-logging for Anthropic"""
    try:
        mlflow.anthropic.autolog()
    except Exception as e:
        print(f"Failed to initialize MLflow tracking: {e}")
        raise

def get_client():
    """Create and return an Anthropic client with API key from environment"""
    # load_dotenv()  # Load environment variables from .env file
    api_key = "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"
    
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    
    return anthropic.Anthropic(api_key=api_key)

def send_message(client, content):
    """Send a message to Claude and handle potential errors"""
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": content},
            ],
        )
        return message.content
    except anthropic.APIError as e:
        print(f"API error occurred: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise

def main():
    """Main function to run the example"""
    setup_mlflow_tracking()
    client = get_client()
    
    try:
        response = send_message(client, "Hello, Claude")
        print(response)
    except Exception as e:
        print(f"Failed to get response: {e}")

if __name__ == "__main__":
    main()