#!/usr/bin/python
import os, sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app import app as application
