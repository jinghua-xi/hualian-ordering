"""Conservative, side-effect-free parsing of pasted procurement text and TSV."""
import csv
from datetime import date, timedelta
import io
import re
from catalog_search import category_from_text, material_matches

DATE = re.compile(r'(?<![A-Za-z0-9])(?:(?P<y>20\d{2})[年./-])(?P<m>\d{1,2})[月./-](?P<d>\d{1,2})日?(?!\d)|(?<![A-Za-z0-9.-])(?P<sm>\d{1,2})[月./-](?P<sd>\d{1,2})日?(?![\dA-Za-z套个只件粒米包袋箱]|\s*(?:mm|毫米))',re.I)
NUMBER = r'(?:[+-]?\d[\d,，]*(?:\.\d+)?|[一二两三四五六七八九十百千万零]+)'
QUANTITY = re.compile(r'(?<![A-Za-z0-9._/半负一二两三四五六七八九十百千万零至到或~～-])(?P<q>'+NUMBER+r')\s*(?P<u>套|个|只|件|粒|米|包|袋|箱)')
HEADERS = {'货号':'sku','型号':'sku','SKU':'sku','品名':'name','商品':'name','名称':'name','商品名称':'name','颜色':'color','规格':'spec','尺寸':'spec','数量':'quantity','订购数量':'quantity','单位':'unit','交期':'date','交付日期':'date','交付时间':'date','期望交期':'date','交货日期':'date','备注':'note'}

def number(value):
    if not isinstance(value,str): return None
    value=value.strip().replace(',','').replace('，','')
    if re.fullmatch(r'\+?\d+',value):
        n=int(value) if len(value)<10 else 0
    elif re.fullmatch(r'[一二两三四五六七八九十百千万零]+',value):
        if re.search(r'[百千万][一二两三四五六七八九]$',value) or re.search(r'[一二两三四五六七八九]{2}',value): return None
        digits=dict(zip('零一二两三四五六七八九',[0,1,2,2,3,4,5,6,7,8,9])); n=part=current=0
        for char in value:
            if char in digits: current=digits[char]
            elif char=='万': n+=(part+current)*10000;part=current=0
            else: part+=(current or 1)*{'十':10,'百':100,'千':1000}[char];current=0
        n+=part+current
    else: return None
    return n if 1<=n<=1000000 else None


def dates(text,today):
    result=[]
    for match in DATE.finditer(text):
        try:
            y=int(match['y']) if match['y'] else today.year
            val=date(y,int(match['m'] or match['sm']),int(match['d'] or match['sd'])).isoformat()
        except ValueError: val=''
        result.append((val,match.group(),not bool(match['y'])))
    return result


def match_products(text, products, fields=None):
    fields=fields or {}
    query=' '.join(str(v) for k,v in fields.items() if k not in ('quantity','date','note','unit')) if fields else re.split(r'备注\s*[:：]',text,maxsplit=1)[0]
    explicit=[]
    for p in products:
        code=p.get('manufacturer_sku') or p['sku']
        if re.search(r'(?<![A-Z0-9-])'+re.escape(code)+r'(?![A-Z0-9-])',query,re.I): explicit.append(p)
        elif p['sku'] != code and re.search(r'(?<![A-Z0-9-])'+re.escape(p['sku'])+r'(?![A-Z0-9-])',query,re.I): explicit.append(p)
    known_codes={p['sku'].upper() for p in products} | {(p.get('manufacturer_sku') or p['sku']).upper() for p in products}
    code_tokens=re.findall(r'(?<![A-Z0-9_-])[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*(?![A-Z0-9_-])',query,re.I)
    unknown_code=any(any(char.isdigit() for char in code) and code.upper() not in known_codes for code in code_tokens)
    if unknown_code or (fields.get('sku') and not explicit): return []
    matches=explicit or list(products)
    colors=[c for c in ('黑色','灰色','白色','红色','蓝色','黄色','绿色','彩色','透明') if c in query]
    if colors:
        matches=[p for p in matches if any((c=='彩色' and ('彩色' in p['name'] or not p.get('color'))) or c==p.get('color') for c in colors)]
    categories=[c for c in sorted({p['category'] for p in products},key=len,reverse=True) if c in query]
    category=category_from_text(query,{p['category'] for p in products})
    if not categories and category: categories=[category]
    if categories: matches=[p for p in matches if p['category'] in categories]
    for material in ('塑料','树脂','尼龙','金属'):
        if material in query: matches=[p for p in matches if material_matches(p,material)]
    size=re.search(r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)',query,re.I)
    if not size and fields.get('spec'): size=re.search(r'\d+(?:\.\d+)?',fields['spec'])
    if size:
        wanted=float(size.group(1) if size.lastindex else size.group())
        matches=[p for p in matches if any(float(v)==wanted for v in re.findall(r'\d+(?:\.\d+)?',p['spec']))]
    if not (explicit or colors or categories or size): return []
    return matches


def parse_paste(text, products, today, source_rows=None):
    """No orders are created. Missing/ambiguous values remain editable, never guessed."""
    if not isinstance(text,str) or not text.strip() or len(text)>12000:
        raise ValueError('请粘贴1至12000字的采购内容。')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',text):
        raise ValueError('粘贴内容含无效控制字符。')
    text=text.replace('–','-').replace('—','-').replace('\r\n','\n')
    raw=list(csv.reader(io.StringIO(text),delimiter='\t')) if '\t' in text else [[line] for line in re.split(r'[\n;；]+|另外|还有|再要',text)]
    raw=[row for row in raw if any(v.strip() for v in row)]
    if len(raw)>101: raise ValueError('一次最多识别100条商品，请分批粘贴。')
    header={i:HEADERS[v.strip()] for i,v in enumerate(raw[0]) if v.strip() in HEADERS} if raw else {}
    if len(header)>=2:
        if len(set(header.values())) != len(header): raise ValueError('存在重复栏目，请只保留一列数量、一列交期等必要栏目。')
        raw=raw[1:]
    else: header={}
    if source_rows is not None:
        raw=[[line] for line in source_rows];header={}
    all_dates=dates(text,today)
    distinct={value for value,_,_ in all_dates if value}
    common=next(iter(distinct)) if len(distinct)==1 and all(value for value,_,_ in all_dates) else ''
    global_issues=[]
    if len(distinct)>1: global_issues.append('识别到多个交期；只填入有明确对应关系的商品，空白处请补填。')
    if common: global_issues.append('只识别到一个交期，已默认应用于全部未注明交期的商品，请核对。')
    if any(yearless for _,_,yearless in all_dates): global_issues.append(f'未注明年份的日期按{today.year}年填写，请核对年份。')
    rows=[];unrecognized=[]
    for cells in raw:
        fields={kind:cells[i].strip() for i,kind in header.items() if i<len(cells)}
        line='\t'.join(cells).strip()
        if not line: continue
        row_dates=dates(fields.get('date',line),today)
        # Standalone common date lines are settings, not products.
        evidence=DATE.sub('',line)
        if not fields and not re.search(r'[A-Za-z]|扣|环|钩',evidence) and row_dates:
            continue
        candidates=match_products(line,products,fields)
        explicit_item=bool(fields or candidates or re.search(r'扣|货号|[A-Z]\d',line,re.I))
        if not explicit_item:
            unrecognized.append(line);continue
        issues=[]
        sku=candidates[0]['sku'] if len(candidates)==1 else ''
        if not candidates: issues.append('未匹配目录商品，请手动选择。')
        elif not sku: issues.append('商品有多个候选，请选择颜色、规格或货号。')
        stripped=DATE.sub(' ',re.split(r'备注\s*[:：]',line,maxsplit=1)[0])
        qty_matches=list(QUANTITY.finditer(stripped))
        unit=fields.get('unit','')
        if 'quantity' in fields:
            qraw=fields['quantity']; qmatch=QUANTITY.fullmatch(qraw)
            quantity=number(qmatch['q'] if qmatch else qraw)
            if qmatch: unit=unit or qmatch['u']
        elif len(qty_matches)==1:
            quantity=number(qty_matches[0]['q']);unit=qty_matches[0]['u']
        else:
            quantity=None
            if not header and len(cells)>1:
                for val in cells[1:]:
                    if re.fullmatch(r'[+-]?\d[\d,]*(?:\.\d+)?',val.strip()):
                        quantity=number(val.strip());break
            if quantity is None:
                qmatch=re.search(r'(?:数量\s*[:：]?\s*|[×xX]\s*)('+NUMBER+r')(?![\d.])',stripped)
                if qmatch: quantity=number(qmatch[1])
        if quantity is None: issues.append('请填写1至1000000的整数数量。')
        if sku and unit and unit != candidates[0]['unit']: issues.append(f'原文单位为{unit}，目录单位为{candidates[0]["unit"]}，请核对数量与单位。')
        unique={value for value,_,_ in row_dates if value}
        if row_dates:
            delivery=next(iter(unique)) if len(unique)==1 and all(v for v,_,_ in row_dates) else ''
        else: delivery=common
        if not delivery: issues.append('请填写该商品的交付日期。')
        elif date.fromisoformat(delivery)<today+timedelta(days=7): issues.append('交付至少需要7天，请调整交期。')
        note=fields.get('note','')
        if not note:
            m=re.search(r'备注\s*[:：]\s*(.+)',line)
            if m: note=m[1]
        if len(note)>100: issues.append('备注超过100字，请缩短。')
        rows.append({'source':line,'sku':sku,'candidates':[p['sku'] for p in candidates], 'quantity':quantity,'unit':unit,'delivery_date':delivery,'note':note,'issues':issues})
    if not rows: raise ValueError('没有识别到商品行，请包含货号或品类，或使用Excel的货号、数量、交期栏目。')
    if len(rows)>100: raise ValueError('一次最多100条商品。')
    if unrecognized: global_issues.append('以下内容未识别为商品，请检查是否有遗漏：'+'；'.join(unrecognized)[:600])
    return {'rows':rows,'warnings':global_issues,'common_date':common,'min_delivery_date':(today+timedelta(days=7)).isoformat(),'source':'规则识别'}
