<div align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge&logo=python" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=for-the-badge&logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Database-Redis-DC382D?style=for-the-badge&logo=redis" alt="Redis" />
  <img src="https://img.shields.io/badge/Container-Docker-2496ED?style=for-the-badge&logo=docker" alt="Docker" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" alt="License" />
</div>

<h1 align="center">🔒 MPC Server: Secure Multi-Party Computation Coordinator</h1>

<p align="center">
  <strong>A robust and scalable FastAPI-based server for coordinating secure multi-party computation (MPC) aggregation tasks, leveraging Redis for state management and Docker for easy deployment.</strong>
</p>

---

## 📖 Overview

This project implements a **Multi-Party Computation (MPC) Coordinator Server** designed to facilitate secure aggregation of data from multiple participants without revealing individual inputs. Built with **FastAPI** for high performance and ease of use, the server utilizes **Redis** as a highly efficient in-memory data store for managing session states and intermediate computation results. The entire application is containerized using **Docker** and orchestrated with **Docker Compose**, ensuring a streamlined development and deployment experience.

This server is ideal for scenarios requiring privacy-preserving data aggregation, such as federated learning, secure statistics, or confidential surveys, where data privacy and integrity are paramount.

### ✨ Key Features

| Feature | Description |
| :--- | :--- |
| 🚀 **FastAPI Backend** | High-performance Python web framework for building robust and scalable API endpoints. |
| 💾 **Redis State Management** | Leverages Redis streams and key-value store for efficient, real-time session management and secure storage of encrypted shares. |
| 🐳 **Dockerized Deployment** | Provides `Dockerfile` and `docker-compose.yml` for easy setup and consistent environments across development and production. |
| 🔒 **Secure Aggregation Protocol** | Implements a secure aggregation protocol allowing multiple parties to contribute to a sum without revealing their individual values. |
| 🔑 **JWT Authentication** | Secures API endpoints using JSON Web Tokens (JWT) for participant authentication and session integrity. |
| 📊 **Dynamic Session Management** | Supports creation and management of multiple MPC sessions, each with configurable parameters like number of parties and session duration. |

---

## 📂 Repository Structure

```text
mpc-server/
├── app/                            # FastAPI application source code
│   ├── main.py                     # Main FastAPI application and API endpoints
│   └── models.py                   # Pydantic models for request/response validation
├── clients/                        # Example client implementations for interacting with the server
│   └── secure_agg_party.py         # Python client for participating in secure aggregation
├── scripts/                        # Utility scripts for development and demonstration
│   └── demo.sh                     # Shell script to run a demonstration of the MPC server and client
├── workers/                        # Background worker processes (if any, currently empty)
├── Dockerfile                      # Dockerfile for building the MPC server image
├── docker-compose.yml              # Docker Compose configuration for multi-service deployment
├── requirements.txt                # Python dependencies for the server
└── README.md                       # This README file
```

---

## 🚀 Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

*   [Docker](https://docs.docker.com/get-docker/) installed
*   [Docker Compose](https://docs.docker.com/compose/install/) installed

### Installation and Setup

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/cperry183/mpc-server.git
    cd mpc-server
    ```

2.  **Build and run the services using Docker Compose:**

    This will build the `mpc-server` Docker image and start both the FastAPI server and a Redis instance.

    ```bash
    docker-compose up --build
    ```

    The MPC server will be accessible at `http://localhost:8000`.

### Running the Demo

To see the secure aggregation in action, you can use the provided `demo.sh` script:

```bash
./scripts/demo.sh
```

This script will:
1.  Start the `mpc-coordinator` server and Redis using Docker Compose.
2.  Create a new MPC session.
3.  Simulate multiple clients joining the session and submitting their encrypted shares.
4.  Retrieve the final aggregated result.

---

## 💡 Usage

### API Endpoints

The MPC server exposes the following key API endpoints:

*   **`POST /session/create`**: Creates a new MPC aggregation session.
    *   **Request Body**: `CreateSessionRequest` (specifies number of parties, metadata).
    *   **Response**: Session ID and JWT token for the coordinator.

*   **`POST /session/{session_id}/join`**: Allows a party to join an existing session.
    *   **Request Body**: `JoinSessionRequest` (party ID).
    *   **Response**: JWT token for the joining party.

*   **`POST /session/{session_id}/submit`**: Submits an encrypted share to the session.
    *   **Request Body**: `SubmitShareRequest` (encrypted share, party ID).
    *   **Authentication**: Requires a valid party JWT token.

*   **`GET /session/{session_id}/result`**: Retrieves the final aggregated result.
    *   **Authentication**: Requires a valid coordinator JWT token.

*For detailed request/response models, refer to `app/main.py` and `app/models.py`.*

### Client Interaction

The `clients/secure_agg_party.py` script demonstrates how a client can interact with the MPC server:

1.  **Generate Keys**: Each party generates a pair of public/private keys.
2.  **Join Session**: Parties join a session using their public key.
3.  **Encrypt and Submit**: Parties encrypt their private value using other parties' public keys and submit the shares.
4.  **Retrieve Result**: The coordinator retrieves the final aggregated result after all shares are submitted.

---

## 🤝 Contributing

We welcome contributions to the MPC Server project! To contribute:

1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/your-feature-name`).
3.  Make your changes and ensure tests pass.
4.  Commit your changes (`git commit -m 'Add new feature'`).
5.  Push to the branch (`git push origin feature/your-feature-name`).
6.  Open a Pull Request.

---

## 📜 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
