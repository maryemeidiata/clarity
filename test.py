from dotenv import load_dotenv
import cohere
import os

load_dotenv()
client = cohere.ClientV2(os.getenv("COHERE_API_KEY"))

response = client.chat(
    model="command-a-03-2025",
    messages=[{"role": "user", "content": "Say hello in 5 words"}]
)
print(response.message.content[0].text)