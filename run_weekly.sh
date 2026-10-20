#!/bin/bash
# Weekly collection for Berlin and New York. Run by launchd every Sunday evening
# (~/Library/LaunchAgents/org.citypulse.weekly.plist). Logs go to logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
stamp=$(date +%Y-%m-%d)

for city in berlin newyork; do
    echo "=== $(date) — $city ===" >> "logs/weekly_${stamp}.log"
    .venv/bin/python -W ignore -u "collect_${city}.py" >> "logs/weekly_${stamp}.log" 2>&1
    echo "exit code: $?" >> "logs/weekly_${stamp}.log"
done

# Weekly HTML report → reports/weekly_report_<week>.html
echo "=== $(date) — report ===" >> "logs/weekly_${stamp}.log"
.venv/bin/python -W ignore -u report.py >> "logs/weekly_${stamp}.log" 2>&1
echo "exit code: $?" >> "logs/weekly_${stamp}.log"

# Back up data/ and reports/ to the private city-pulse-data repo. It is tracked
# from this folder via .git-data; -f because the public repo's .gitignore
# excludes both folders.
echo "=== $(date) — data backup ===" >> "logs/weekly_${stamp}.log"
gitdata() { git --git-dir=.git-data --work-tree=. "$@"; }
{
    gitdata add -f -A -- data reports ':(exclude)**/.DS_Store' &&
    gitdata commit -q -m "Weekly data ${stamp}" &&
    gitdata push -q origin main
} >> "logs/weekly_${stamp}.log" 2>&1
echo "exit code: $?" >> "logs/weekly_${stamp}.log"
