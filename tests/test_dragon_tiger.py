from pathlib import Path
from src.dragon_tiger import FetchResult, normalize_records, run

def raw_record(code="000001",secucode="000001.SZ"):
    return {"SECURITY_CODE":code,"SECUCODE":secucode,"SECURITY_NAME_ABBR":"测试股份","TRADE_DATE":"2026-08-11 00:00:00","EXPLAIN":"1家机构买入","CLOSE_PRICE":12.34,"CHANGE_RATE":10.01,"BILLBOARD_NET_AMT":12345678,"BILLBOARD_BUY_AMT":22345678,"BILLBOARD_SELL_AMT":10000000,"BILLBOARD_DEAL_AMT":32345678,"ACCUM_AMOUNT":100000000,"DEAL_NET_RATIO":12.345,"DEAL_AMOUNT_RATIO":32.345,"TURNOVERRATE":20.5,"FREE_MARKET_CAP":5000000000,"EXPLANATION":"日涨幅偏离值达到7%","SECURITY_TYPE_CODE":"058001001"}
def test_market_filter():
    accepted,excluded=normalize_records([raw_record(),raw_record("688001","688001.SH"),raw_record("832001","832001.BJ"),raw_record("123001","123001.SZ")],"2026-08-11")
    assert [x["security_code"] for x in accepted]==["000001","688001"] and excluded==2
class Source:
    def fetch(self,date): return FetchResult([raw_record()],1,"test")
class Drive:
    def __init__(self): self.calls=[]
    def ping(self): return {"service":"test"}
    def upload_dataset_file(self,path,*,dataset,data_date): self.calls.append(path.name); return {"status":"created","file_id":str(len(self.calls)),"drive_path":path.name}
def test_run(tmp_path:Path):
    drive=Drive(); result=run(trade_date="2026-08-11",output_dir=tmp_path,upload_drive=True,source_client=Source(),drive_client=drive)
    assert result["phase"]=="complete" and result["drive_created"]==3 and result["drive_failed"]==0 and len(drive.calls)==3
    assert (tmp_path/"2026-08-11"/"龙虎榜_2026-08-11.csv").exists()
