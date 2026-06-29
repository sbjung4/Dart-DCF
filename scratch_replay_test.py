"""
삼성전자 2023년 실제 매출실적 표 데이터를 그대로 넣어서
dart_api.py의 _parse_revenue_table()이 제대로 동작하는지 확인하는 테스트.

DART API를 호출하지 않고, 이미 받은 진단(table_diagnostic) 데이터를
그대로 XML로 재구성해서 파싱 결과만 확인한다.

실행: python scratch_replay_test.py
(dart_api.py와 같은 폴더에서 실행해야 한다)
"""
import xml.etree.ElementTree as ET
import dart_api as d

print("dart_api.py 위치:", d.__file__)

xml = """<DOCUMENT><BODY><SECTION-1 ATOC="II. 사업의 내용">
<P>4. 매출 및 수주상황</P><P>가. 매출실적</P><P>(단위 : 억원)</P>
<TABLE>
<TR><TD>부  문</TD><TD>매출유형</TD><TD>품   목</TD><TD>제55기</TD><TD>제54기</TD><TD>제53기</TD></TR>
<TR><TD>DX 부문</TD><TD>제ㆍ상품,용역 및기타매출</TD><TD>TV, 모니터,냉장고, 세탁기,에어컨, 스마트폰,네트워크시스템,컴퓨터 등</TD><TD>1,699,923</TD><TD>1,824,897</TD><TD>1,662,594</TD></TR>
<TR><TD>DS 부문</TD><TD>제ㆍ상품,용역 및기타매출</TD><TD>DRAM,NAND Flash,모바일AP 등</TD><TD>665,945</TD><TD>984,553</TD><TD>953,872</TD></TR>
<TR><TD>SDC</TD><TD>제ㆍ상품,용역 및기타매출</TD><TD>스마트폰용 OLED 패널 등</TD><TD>309,754</TD><TD>343,826</TD><TD>317,125</TD></TR>
<TR><TD>Harman</TD><TD>제ㆍ상품,용역 및기타매출</TD><TD>디지털 콕핏, 카오디오, 포터블 스피커 등</TD><TD>143,885</TD><TD>132,137</TD><TD>100,399</TD></TR>
<TR><TD>기   타</TD><TD>부문간 내부거래 제거 등</TD><TD>△230,152</TD><TD>△263,099</TD><TD>△237,942</TD></TR>
<TR><TD>합     계</TD><TD>2,589,355</TD><TD>3,022,314</TD><TD>2,796,048</TD></TR>
</TABLE>
</SECTION-1></BODY></DOCUMENT>"""

root = ET.fromstring(xml)
for el in root.iter():
    if d._local_tag(el.tag).lower() == 'table':
        result = d._parse_revenue_table(el)
        print("파싱 결과:", result)
        expected_harman = 143885.0
        if result.get('Harman') == expected_harman:
            print(f"✅ 정상: Harman = {expected_harman} (단위: 억원)")
        else:
            print(f"❌ 비정상: Harman = {result.get('Harman')}, 기대값 = {expected_harman}")
        break
