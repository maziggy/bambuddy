#!/bin/bash

cd ../bambuddy-website
git add .
git commit -m "Updated website"
git push

cd ../bambuddy-wiki
git add .
git commit -m "Updated Wiki"
git push

cd ../bambuddy-languages
git add .
git commit -m "Updated Bambuddy Languages"
git push

cd ../spoolbuddy-website
git add .
git commit -m "Updated website"
git push

cd ../spoolbuddy-wiki
git add .
git commit -m "Updated Wiki"
git push
