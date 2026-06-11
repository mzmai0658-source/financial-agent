# Database Dump

`financial_report.sql` is a small MySQL dump for course/demo usage. It contains schema and data for:

- `income_sheet`
- `balance_sheet`
- `cash_flow_sheet`
- `core_performance_indicators_sheet`

Each table has 120 rows.

Import on another computer:

```powershell
mysql --default-character-set=utf8mb4 -u root -p < database\financial_report.sql
```

Then update `.env` with the local MySQL username/password and keep:

```text
DB_NAME=financial_report
```

The dump creates `financial_report` if it does not exist and drops/recreates the four application tables before inserting data.
