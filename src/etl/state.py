from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime

@dataclass
class AgentState:
    """
    Shared state for the multi-agent system.
    """
    # Context
    user_query: str = ""
    current_step: str = "idle"
    history: List[Dict[str, str]] = field(default_factory=list)
    
    # Data Context
    files_to_process: List[str] = field(default_factory=list)
    extracted_data: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata Context (for current file)
    current_file_path: Optional[str] = None
    current_stock_code: Optional[str] = None
    current_stock_abbr: Optional[str] = None
    current_report_year: Optional[int] = None
    current_report_period: Optional[str] = None
    
    # Output
    final_answer: Optional[str] = None
    sql_query: Optional[str] = None
    sql_result: Optional[List[Dict[str, Any]]] = None

    def update_metadata(self, code, abbr, year, period):
        self.current_stock_code = code
        self.current_stock_abbr = abbr
        self.current_report_year = year
        self.current_report_period = period

    def clear_metadata(self):
        self.current_stock_code = None
        self.current_stock_abbr = None
        self.current_report_year = None
        self.current_report_period = None
