import multiprocessing

# The recommended formula is 2 * CPU_CORES + 1
# For a 4 vCPU server, 2 * 4 + 1 = 9 workers
workers = 9

# Bind to localhost on port 8000
bind = "127.0.0.1:8000"

# Maximum number of pending connections
backlog = 2048

# Workers silent for more than this many seconds are killed and restarted
timeout = 30

# Restart workers after this many requests to prevent memory leaks
max_requests = 1000
max_requests_jitter = 50

# Log configuration
loglevel = 'info'
accesslog = '-' # Log to stdout
errorlog = '-'  # Log to stderr
