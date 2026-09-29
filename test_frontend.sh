#!/bin/sh

cd frontend
npm run typecheck
npm run lint
npm run test:run
cd ..
