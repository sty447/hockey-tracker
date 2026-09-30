"""Fetch confirmed NHL trades and current rosters. --check makes no writes."""
import argparse,concurrent.futures,datetime,html,json,re,subprocess
from html.parser import HTMLParser
from pathlib import Path
root=Path(__file__).resolve().parents[1];dist=root/'dist'
URL='https://www.nhl.com/news/topic/trade-coverage/2026-27-nhl-trades'
TEAMS={'Anaheim Ducks':'ANA','Boston Bruins':'BOS','Buffalo Sabres':'BUF','Calgary Flames':'CGY','Carolina Hurricanes':'CAR','Chicago Blackhawks':'CHI','Colorado Avalanche':'COL','Columbus Blue Jackets':'CBJ','Dallas Stars':'DAL','Detroit Red Wings':'DET','Edmonton Oilers':'EDM','Florida Panthers':'FLA','Los Angeles Kings':'LAK','Minnesota Wild':'MIN','Montreal Canadiens':'MTL','Nashville Predators':'NSH','New Jersey Devils':'NJD','New York Islanders':'NYI','New York Rangers':'NYR','Ottawa Senators':'OTT','Philadelphia Flyers':'PHI','Pittsburgh Penguins':'PIT','San Jose Sharks':'SJS','Seattle Kraken':'SEA','St. Louis Blues':'STL','Tampa Bay Lightning':'TBL','Toronto Maple Leafs':'TOR','Utah Mammoth':'UTA','Vancouver Canucks':'VAN','Vegas Golden Knights':'VGK','Washington Capitals':'WSH','Winnipeg Jets':'WPG'}
def download(url):return subprocess.check_output(['curl','--fail','--retry','2','--max-time','25','-sS','--compressed',url]).decode()
class StructuredArticle(HTMLParser):
 def __init__(self):super().__init__();self.active=False;self.buffer=[];self.articles=[]
 def handle_starttag(self,tag,attrs):
  if tag=='script' and dict(attrs).get('type')=='application/ld+json':self.active=True;self.buffer=[]
 def handle_data(self,s):
  if self.active:self.buffer.append(s)
 def handle_endtag(self,tag):
  if tag=='script' and self.active:
   self.active=False
   try:
    data=json.loads(''.join(self.buffer));self.articles.extend(data if isinstance(data,list) else [data])
   except ValueError:pass

def parse_trades(page):
 parser=StructuredArticle();parser.feed(page);body=next((x['articleBody'] for x in parser.articles if isinstance(x,dict) and 'articleBody' in x),'')
 if not body:raise ValueError('NHL article structure changed; keeping previous data')
 pattern=r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER) (\d{1,2}):\*?\*?\s*(.*?)\s*\|'
 trades=[]
 for month,day,sentence in re.findall(pattern,body,re.S):
  sentence=html.unescape(sentence).replace('*','').strip().rstrip('.')
  m=re.fullmatch(r'(.+?) acquire (.+?) from (?:the )?(.+?) for (.+)',sentence)
  if not m:raise ValueError('Unrecognized trade format: '+sentence)
  a,gets,b,gives=m.groups()
  if a not in TEAMS or b not in TEAMS:raise ValueError('Unrecognized NHL team')
  def assets(text):
   year=re.search(r'(20\d{2}) NHL Draft',text)
   parts=re.split(r',?\s+and\s+|,\s+',text)
   cleaned=[]
   for part in parts:
    part=re.sub(r'^(?:(?:a|an|the)\s+)?(?:forwards?|defensemen|defenseman|goalies?|goaltenders?)\s+','',part,flags=re.I)
    part=re.sub(r'^(?:a|an)\s+','',part,flags=re.I)
    if 'pick' in part and year and not re.search(r'20\d{2}',part):part+=' in '+year.group(1)+' NHL Draft'
    cleaned.append(part.strip())
   return cleaned
  month_number=datetime.datetime.strptime(month,'%B').month
  draft_year=2026 if month_number>=7 else 2027
  date=datetime.datetime.strptime(f'{month} {day} {draft_year}','%B %d %Y').date().isoformat()
  if date<=datetime.date.today().isoformat():trades.append({'date':date,'teams':[{'team':TEAMS[a],'receives':assets(gets)},{'team':TEAMS[b],'receives':assets(gives)}]})
 if not trades:raise ValueError('No confirmed trades found; keeping previous data')
 return trades

def main():
 args=argparse.ArgumentParser();args.add_argument('--check',action='store_true');args=args.parse_args()
 trades=parse_trades(download(URL))
 def roster(team):return team,json.loads(download(f'https://api-web.nhle.com/v1/roster/{team}/20262027'))
 teams={};players=[];goalies=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  for team,data in pool.map(roster,TEAMS.values()):
   if len(data.get('forwards',[]))<8 or len(data.get('defensemen',[]))<4 or not data.get('goalies'):raise ValueError('Incomplete roster for '+team)
   teams[team]={}
   for group,pos in [('forwards','F'),('defensemen','D'),('goalies','G')]:
    teams[team][group]=[]
    for x in data[group]:
     pid=str(x['id']);name=x['firstName']['default']+' '+x['lastName']['default'];teams[team][group].append(pid)
     (goalies if pos=='G' else players).append({'id':pid,'name':name,'team':team,**({} if pos=='G' else {'position':pos})})
 # Seasonal roster endpoints can omit active/injured players. Verify omissions
 # against each player's official current-team profile before adding them.
 present={x['id'] for x in players+goalies}
 candidates=json.loads((dist/'nhl-roster-candidates.json').read_text())
 previous=[]
 for filename,key in [('nhl-2026-27-roster-players.json','players'),('nhl-2026-27-roster-goalies.json','goalies')]:
  if (dist/filename).exists():previous.extend(json.loads((dist/filename).read_text())[key])
 missing={str(x['id']):x for x in candidates+previous if str(x['id']) not in present}
 recovered=[];inactive=[]
 def profile(item):
  pid,x=item;d=json.loads(download(f'https://api-web.nhle.com/v1/player/{pid}/landing'))
  team=d.get('currentTeamAbbrev');pos=d.get('position')
  if d.get('isActive') is not True or team not in teams: return None
  if pos not in ['C','L','R','LW','RW','F','D','G']:raise ValueError('Unknown player position for '+pid)
  name=d['firstName']['default']+' '+d['lastName']['default'];is_goalie=pos=='G'
  return {'id':pid,'name':name,'team':team,**({} if is_goalie else {'position':'D' if pos=='D' else 'F'})},is_goalie
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  for result in pool.map(profile,missing.items()):
   if result is None:continue
   row,is_goalie=result;group='goalies' if is_goalie else 'defensemen' if row['position']=='D' else 'forwards'
   teams[row['team']][group].append(row['id']);(goalies if is_goalie else players).append(row);recovered.append(row)
 print(f'Profile checks recovered {sum("position" in x for x in recovered)} skaters and {sum("position" not in x for x in recovered)} goalies.')
 ids=[x['id'] for x in players+goalies]
 if len(ids)!=len(set(ids)):raise ValueError('Duplicate NHL player IDs; keeping previous data')
 print(f'Validated {len(trades)} trades, {len(teams)} teams, {len(players)} skaters and {len(goalies)} goalies.')
 if args.check:return
 now=datetime.datetime.now(datetime.timezone.utc).isoformat();day=now[:10]
 files={'nhl-recent-trades.json':{'asOf':day,'checkedAt':now,'source':URL,'trades':trades},'nhl-2026-27-rosters.json':{'asOf':now,'teams':teams},'nhl-2026-27-roster-players.json':{'asOf':now,'players':players},'nhl-2026-27-roster-goalies.json':{'asOf':now,'goalies':goalies}}
 for file,data in files.items():
  target=dist/file;temp=target.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(target)
 subprocess.run(['node',str(root/'scripts/sync-trade-rosters.cjs')],check=True,cwd=root)
 bundle={'asOf':now,'trades':json.loads((dist/'nhl-recent-trades.json').read_text()),'rosters':json.loads((dist/'nhl-2026-27-rosters.json').read_text()),'skaters':json.loads((dist/'nhl-2026-27-roster-players.json').read_text()),'goalies':json.loads((dist/'nhl-2026-27-roster-goalies.json').read_text())}
 (dist/'nhl-live-data.json').write_text(json.dumps(bundle,indent=2)+'\n')
if __name__=='__main__':main()
