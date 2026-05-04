#!/bin/sh

export LC_ALL=C.UTF-8

cd frontend
npx tsc
npm run lint
npm run test:run
cd ..
