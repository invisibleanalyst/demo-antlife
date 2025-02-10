### 📦 Installation

PandasAI platform uses a dockerized client-server architecture. You will need to have Docker installed on your machine.

```bash
git clone https://github.com/invisibleanalyst/demo-antlife
cd demo-antlife
docker-compose build
```

### 🔧 Setting Up Environment Variables

Before running the platform, you need to configure environment variables. This ensures that the application has access to the required API keys and settings.

Run the following commands to create `.env` files for both the client and the server:

```bash
cp client/env.example client/.env
cp server/env.example server/.env
```

Next, open the `.env` file inside the `server` directory and add your PandasAI API key:

```bash
nano server/.env
```

Locate the line for `PANDASAI_API_KEY` and update it with your API key from [PandaBI](https://pandabi.ai):

```plaintext
PANDASAI_API_KEY=your_api_key_here
```

Save and exit the file. This step is crucial as the API key is required for processing natural language queries using PandasAI.

### 🚀 Running the platform

Once you have set up the environment variables and built the platform, you can start it with:

```bash
docker-compose up
```

This will start the client and server, and you can access the client at `http://localhost:3000`.

### 🛠 Troubleshooting

If you encounter any issues during setup, try the following:

1. Ensure Docker is installed and running on your machine.
2. Verify that the `.env` files exist in both the `client` and `server` directories.
3. Check the logs for errors by running:
   ```bash
   docker-compose logs
   ```
4. Restart the services:
   ```bash
   docker-compose down && docker-compose up --build
   ```
5. If API authentication fails, ensure your API key is correctly added to `server/.env`.
