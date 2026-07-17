#!/bin/bash

cd /opt/claude/projects/bambuddy-website
git add .
git commit -m "Updated website"
git push

cd /opt/claude/projects/bambuddy-wiki
git add .
git commit -m "Updated Wiki"
git push

#cd /opt/claude/projects/bambuddy-sponsors-portal
#git add .
#git commit -m "Updated portal"
#git push
