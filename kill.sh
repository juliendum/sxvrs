#!/bin/bash
kill $(ps aux | grep "python sxvrs_" | awk '{print $2}')