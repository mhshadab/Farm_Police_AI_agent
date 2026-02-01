# Farm Police AI — Agentic Incident Response System

Farm Police AI is a Python-based system that integrates with an IBM watsonx Orchestrate **Farm Incident Response Agent** to automate incident analysis, notifications, work-order tracking, and visualization.

---

## What It Does

- Accepts raw sensor or human incident input  
- Sends input to an AI agent for incident analysis  
- Automatically emails Operations and Maintenance  
- Creates persistent, deduplicated work orders  
- Visualizes work-order severity over time  

---

## Project Files

```
Farm_Police_Ai/
├── main_chat.py        # Main CLI application
├── apikey.txt          # IBM Cloud API key (one line)
├── workorders.db       # Persistent SQLite database
├── workorders.db-wal   # SQLite WAL file (normal)
├── workorders.db-shm   # SQLite SHM file (normal)
└── README.md
```

---

## Requirements

- Python 3.13+
- Install dependencies:
  ```bash
  python -m pip install requests matplotlib
  ```

---

## Setup  ############################################### IMPORTANT

1. In `apikey.txt` paste our IBM Cloud API key "3002124 - watsonx".
2. Ensure internet access for watsonx Orchestrate and Google Apps Script email service.

---

## Running Instructions

```bash
python main_chat.py
```

- Enter sensor or incident data when prompted  
- The system analyzes the incident using AI  
- Notification emails are sent automatically  
- Work orders are stored persistently  
- A severity vs time chart is displayed  
- Press **Enter** on empty input to exit  

---

## Notes

- Work orders persist across program runs  
- Duplicate incidents are not reinserted  
- SQLite WAL/SHM files are expected  
- Severity is determined by the AI agent  

---

## Demo Value

This project demonstrates how agentic AI can move beyond chat to enable real operational automation, including incident triage, alerting, ticketing, and dashboard-style monitoring.

