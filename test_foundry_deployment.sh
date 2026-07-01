#!/bin/bash

echo "Testing foundry-local service deployment..."

# Check if the Dockerfile is valid
echo "Validating Dockerfile..."
docker build -t foundry-local-test -f /home/hanno/runtime/services/foundry-local/Dockerfile /home/hanno/runtime/services/foundry-local

if [ $? -eq 0 ]; then
    echo "✅ Dockerfile validation successful"
else
    echo "❌ Dockerfile validation failed"
    exit 1
fi

# Try to run a simple test
echo "Testing service startup..."
docker run --rm --gpus '"device=1"' -p 8100:8100 foundry-local-test python -c "import server; print('Service imports successfully')"

echo "Test completed."