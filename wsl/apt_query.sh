#!/bin/bash
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99ipv4
export DEBIAN_FRONTEND=noninteractive
timeout 500 apt-get update -qq 2>&1 | tail -3
apt-cache policy odr-dabmux odr-dabmod odr-audioenc odr-padenc welle.io | grep -E '^[a-z]|Candidate'
