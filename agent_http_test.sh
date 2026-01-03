#!/bin/bash

# Step 1: Get auth token
TOKEN=$(curl -s -X POST http://127.0.0.1:40080/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"user_id": "cli-user"}' | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

# Step 2: Run the task
curl -X POST http://127.0.0.1:40080/api/v1/sessions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "task": "The the random fact about cats usin meow skill.",
    "config": {
      "profile": "/Users/greg/EXTRACTUM/Agentum/Project/tests/core-tests/input/permissions.user.permissive.yaml"
    }
  }'