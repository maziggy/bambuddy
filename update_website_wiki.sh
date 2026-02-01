#!/bin/bash

cd ../bambuddy-website
git add .
git commit -m "Updated website"
git push

cd ../bambuddy-wiki
git add .
git commit -m "Updated Wiki"
git push

cd ../spoolbuddy-website
git add .
git commit -m "Updated website"
git push

cd ../spoolbuddy-wiki
git add .
git commit -m "Updated Wiki"
git push
