from app.main import evaluate

PROGRAM={"rules":[{"field":"region","operator":"eq","value":"spb","weight":60,"required":True,"explanation":"Регион"},{"field":"industry","operator":"exists","value":True,"weight":40,"required":False,"explanation":"Отрасль"}]}
def test_eligible(): assert evaluate(PROGRAM,{"region":"spb","industry":"it"})["status"] == "eligible"
def test_required_mismatch(): assert evaluate(PROGRAM,{"region":"other","industry":"it"})["status"] == "not_eligible"
def test_unknown_needs_check(): assert evaluate(PROGRAM,{"region":"spb"})["status"] == "check_needed"
