import os

def check_openai_api_key():
  """Checks if the OPENAI_API_KEY environment variable is set.

  Returns:
      bool: True if the environment variable is set, False otherwise.
  """
  return "OPENAI_API_KEY" in os.environ

if __name__ == "__main__":
  if check_openai_api_key():
    print("OPENAI_API_KEY environment variable is set.")
  else:
    print("OPENAI_API_KEY environment variable is NOT set.")
    print("Please set it before running the application.")
    # Optionally, exit the program if the key is essential
    # exit(1)