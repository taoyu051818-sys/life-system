import os

import uvicorn


if __name__ == "__main__":
    host = os.getenv("LIFE_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("LIFE_WEB_PORT", "8080"))
    uvicorn.run("life_system.web.app:create_app", host=host, port=port, reload=False, factory=True)
