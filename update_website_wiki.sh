#!/bin/bash

cd ../bambuddy-website
git add .
git commit -m "Updated website"
git push

cd ../bambuddy-wiki
git add .
git commit -m "Updated Wiki"
git push

cd ../bambuddy-telemetry/
git add .
git commit -m "Updated Stats"
git push
