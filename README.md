# Zillow.com Realtime Scraper API

A Django REST Framework API that scrapes real estate data from Zillow.com in real-time.

## Features

- **Real-time Scraping**: Fresh data without caching
- **14 API Endpoints**: Comprehensive coverage of Zillow data
- **Proxy Rotation**: Avoid detection with automatic proxy rotation
- **User-Agent Rotation**: Mimic different browsers
- **Rate Limiting**: Built-in rate limiting per user
- **Swagger Documentation**: Auto-generated API docs

## Quick Start

### Prerequisites

- Docker and Docker Compose
- (Optional) Python 3.11+ for local development

### Using Docker

1. Clone the repository and navigate to the project:

   ```bash
   cd rapidapi-repl
   ```

2. Copy the environment file:

   ```bash
   cp .env.example .env
   ```

3. Start the services:

   ```bash
   docker-compose up --build
   ```

4. Run migrations:

   ```bash
   docker-compose exec web python manage.py migrate
   ```

5. Access the API:
   - API: http://localhost:8112
   - Swagger Docs: http://localhost:8112/api/docs/
   - ReDoc: http://localhost:8112/api/redoc/

### Local Development

1. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables (or use .env file)

4. Run migrations:

   ```bash
   python manage.py migrate
   ```

5. Start the server:
   ```bash
   python manage.py runserver
   ```

## API Endpoints

### Agents

| Endpoint                      | Description                     |
| ----------------------------- | ------------------------------- |
| `GET /agentByLocation`        | Get agents by location          |
| `GET /agentInfo`              | Get agent profile information   |
| `GET /agentReviews`           | Get agent reviews               |
| `GET /agentForSaleProperties` | Get agent's for-sale properties |
| `GET /agentForRentProperties` | Get agent's rental properties   |
| `GET /agentSoldProperties`    | Get agent's sold properties     |

### Properties

| Endpoint                | Description           |
| ----------------------- | --------------------- |
| `GET /bylocation`       | Search by location    |
| `GET /bycoordinates`    | Search by coordinates |
| `GET /bymapbounds`      | Search by map bounds  |
| `GET /bymlsid`          | Search by MLS ID      |
| `GET /bypolygon`        | Search by polygon     |
| `GET /byurl`            | Parse Zillow URL      |
| `GET /apartmentDetails` | Get apartment details |
| `GET /autocomplete`     | Location autocomplete |

## Configuration

Edit `.env` file to configure:

```bash
# Proxies (comma-separated)
PROXIES=http://proxy1:8080,http://proxy2:8080

# Rate limiting
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_PER_HOUR=500

# Scraper delays
REQUEST_DELAY_MIN=1.0
REQUEST_DELAY_MAX=3.0
```

## Testing

```bash
# Run tests
docker-compose exec web python manage.py test

# Or locally
python manage.py test
```

## Project Structure

```
rapidapi-repl/
├── api/                  # API app (views, serializers, models)
├── core/                 # Core utilities (proxy, user-agent, rate limiter)
├── scrapers/             # Scraper services
├── zillow_scraper/       # Django project settings
├── docker-compose.yml    # Docker configuration
└── requirements.txt      # Python dependencies
```

## Legal Notice

> ⚠️ **Warning**: Web scraping may violate Zillow's Terms of Service. Use responsibly and ensure compliance with all applicable laws and regulations.

## License

MIT License
