#!/bin/sh

cd frontend
npm run lint
npm run test:run
cd ..
