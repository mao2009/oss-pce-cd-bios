// Synthetic multi-byte HuC6280 block transfer matrix on pinned Geargrafx.
// Verification-only preflight/byte hooks; this is not a production BIOS gate.
#include "geargrafx_core.h"
#include "huc6270.h"
#include "huc6280.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

extern bool (*gg_verify_block_write_guard)(void*, unsigned, unsigned);
extern bool (*gg_verify_transfer_start_guard)(void*, unsigned);
extern bool (*gg_verify_transfer_abort_guard)(void*, unsigned, unsigned, unsigned, unsigned);
extern void (*gg_verify_block_byte_observer)(void*, unsigned, unsigned, unsigned);
extern void* gg_verify_block_write_context;

namespace {
const char* const kNames[] = {"tii", "tin", "tia", "tdd", "tai"};
const unsigned kOpcode[] = {0x73, 0xD3, 0xE3, 0xC3, 0xF3};
const u8 kPattern[] = {0x13,0x27,0x3B,0x4F,0x63,0x77,0x8B,0x9F};
Memory* g_mem=nullptr;
HuC6280::HuC6280_State* g_cpu=nullptr;
bool g_deny=false;
unsigned g_pre_calls=0,g_pre_allow=0,g_pre_deny=0,g_byte_calls=0,g_start_calls=0;
unsigned g_op=0,g_src=0,g_dst=0,g_len=0;
unsigned g_pre_a=0,g_pre_x=0,g_pre_y=0,g_pre_p=0,g_pre_sp=0;
std::vector<unsigned> g_plan_src,g_plan_dst,g_plan_src_bank,g_plan_dst_bank,g_byte_src,g_byte_dst;
std::vector<int> g_plan_src_phys,g_plan_dst_phys;
int op_index(const std::string& s) { for(int i=0;i<5;++i) if(s==kNames[i]) return i; return -1; }
int phys_wram(u16 a) {
    u8 bank=g_mem->GetMpr(a>>13);
    if(g_mem->GetBankType(bank)!=Memory::MEMORY_BANK_TYPE_WRAM) return -1;
    return (int)(g_mem->GetMemoryMap()[bank]-g_mem->GetWorkingRAM())+(a&0x1FFF);
}
void record(std::vector<unsigned>& logical,std::vector<unsigned>& banks,std::vector<int>& phys,u16 a) {
    u8 bank=g_mem->GetMpr(a>>13); logical.push_back(a);banks.push_back(bank);phys.push_back(phys_wram(a));
}
bool preflight(void*,unsigned op,unsigned src,unsigned dst,unsigned len) {
    ++g_pre_calls;g_op=op;g_src=src;g_dst=dst;g_len=len;
    g_pre_a=g_cpu->A->GetValue();g_pre_x=g_cpu->X->GetValue();g_pre_y=g_cpu->Y->GetValue();
    g_pre_p=g_cpu->P->GetValue();g_pre_sp=g_cpu->S->GetValue();
    g_plan_src.clear();g_plan_dst.clear();g_plan_src_bank.clear();g_plan_dst_bank.clear();
    g_plan_src_phys.clear();g_plan_dst_phys.clear();
    if(op>=5 || len==0 || len>8) {++g_pre_deny;return false;}
    unsigned s=src,d=dst;bool safe=true;
    for(unsigned i=0;i<len;++i) {
        record(g_plan_src,g_plan_src_bank,g_plan_src_phys,(u16)s);
        record(g_plan_dst,g_plan_dst_bank,g_plan_dst_phys,(u16)d);
        if(g_plan_src_phys.back()<0||g_plan_dst_phys.back()<0) safe=false;
        switch(op) {
        case 0:s=(u16)(s+1);d=(u16)(d+1);break;
        case 1:s=(u16)(s+1);break;
        case 2:s=(u16)(s+1);d=(u16)(d+((i&1)?-1:1));break;
        case 3:s=(u16)(s-1);d=(u16)(d-1);break;
        case 4:s=(u16)(s+((i&1)?-1:1));d=(u16)(d+1);break;
        }
    }
    if(safe){++g_pre_allow;return true;} ++g_pre_deny;return false;
}
bool byte_guard(void*,unsigned,unsigned) { ++g_byte_calls; return true; }
bool start_guard(void*,unsigned) { ++g_start_calls;return true; }
void observe(void*,unsigned,unsigned s,unsigned d) { g_byte_src.push_back((u16)s);g_byte_dst.push_back((u16)d); }
}

int main(int argc,char** argv) {
    if(argc!=4)return 2;
    std::string name=argv[3];size_t sep=name.find('-');if(sep==std::string::npos)return 2;
    int op=op_index(name.substr(0,sep));std::string scenario=name.substr(sep+1);if(op<0)return 2;
    unsigned len=0,src=0x3E80,dst=0x3E90,guest=0x3E00;
    bool tam=false,deny=false,overlap=scenario=="alias-overlap";
    if(scenario.rfind("len",0)==0)len=std::strtoul(scenario.c_str()+3,nullptr,10);
    else if(scenario=="boundary-1fff"){len=2;dst=0x1FFF;}
    else if(scenario=="boundary-3fff"){len=2;dst=0x3FFF;}
    else if(scenario=="boundary-7fff"){len=2;dst=0x7FFF;}
    else if(scenario=="boundary-ffff"){len=2;dst=0xFFFF;}
    else if(scenario=="boundary-alias"){len=2;dst=0x3FFF;}
    else if(scenario=="reverse-4000"){len=2;dst=0x4000;}
    else if(scenario=="deny-hw"||scenario=="deny-rom"||scenario=="deny-backup"){
        deny=true;len=2;guest=0x1E00;src=0x1E80;dst=(op==1||op==3)?0x4000:0x3FFF;
    } else if(scenario=="deny-nonmono"){deny=true;len=3;dst=0x3FFF;}
    else if(scenario=="tam-alias"){tam=true;len=2;src=0x3E80;dst=0x4010;}
    else if(scenario=="tam-deny"){tam=true;deny=true;len=2;src=0x3E80;dst=0x4010;}
    else if(overlap){if(op!=0)return 2;len=3;src=0x4000;dst=0x2001;}
    else return 2;
    if(op==3&&scenario.rfind("len",0)==0){src+=len-1;dst+=len-1;}
    if(!len||len>8)return 2;
    g_deny=deny;
    std::ifstream file(argv[1],std::ios::binary);std::vector<u8> bios((std::istreambuf_iterator<char>(file)),{});
    if(bios.size()<0x2000)return 3;
    GeargrafxCore core;core.Init(nullptr);if(!core.LoadBiosFromBuffer(bios.data(),bios.size(),true)||!core.LoadMedia(argv[2]))return 4;
    Memory* mem=core.GetMemory();g_mem=mem;HuC6280* cpu=core.GetHuC6280();auto* st=cpu->GetState();g_cpu=st;auto* vs=core.GetHuC6270_1()->GetState();
    for(int p=0;p<8;++p)mem->SetMpr(p,0xF8);
    if(deny){
        if(scenario=="deny-nonmono")mem->SetMpr(2,0xFF);
        if(scenario=="deny-hw"){if(op==3)mem->SetMpr(1,0xFF);else mem->SetMpr(2,0xFF);}
        if(scenario=="deny-rom"){if(op==3)mem->SetMpr(1,0x00);else mem->SetMpr(2,0x00);}
        if(scenario=="deny-backup"){mem->EnableBackupRam(true);if(op==3)mem->SetMpr(1,0xF7);else mem->SetMpr(2,0xF7);}
    }
    if(overlap||scenario=="boundary-alias")mem->SetMpr(2,0xF9);
    std::vector<u8> code;
    if(tam){code={0xA9,(u8)(deny?0xFF:0xF9),0x53,0x04};mem->SetMpr(2,0xF8);}
    code.push_back((u8)kOpcode[op]);code.push_back((u8)src);code.push_back((u8)(src>>8));code.push_back((u8)dst);code.push_back((u8)(dst>>8));code.push_back((u8)len);code.push_back((u8)(len>>8));
    unsigned bra_pc=guest+(unsigned)code.size();code.push_back(0x80);code.push_back(0xFE);
    int gi=phys_wram(guest);if(gi<0)return 5;u8* wram=mem->GetWorkingRAM();for(size_t i=0;i<code.size();++i)wram[gi+i]=code[i];
    // Seed ordered, distinguishable bytes using the source update pattern.
    unsigned s=src;for(unsigned i=0;i<len;++i){int ix=phys_wram(s);if(ix<0)return 6;wram[ix]=overlap?(u8)(0x11+0x11*i):kPattern[i];switch(op){case 0:case 1:case 2:s=(u16)(s+1);break;case 3:s=(u16)(s-1);break;case 4:s=(u16)(s+((i&1)?-1:1));break;}}
    unsigned d=dst;for(unsigned i=0;i<len;++i){int ix=phys_wram(d);if(ix>=0&&!(overlap&&ix<4))wram[ix]=0xCC;switch(op){case 0:case 4:d=(u16)(d+1);break;case 1:break;case 2:d=(u16)(d+((i&1)?-1:1));break;case 3:d=(u16)(d-1);break;}}
    wram[0x1FD]=wram[0x1FE]=wram[0x1FF]=0;
    st->PC->SetValue((u16)guest);st->S->SetValue(0xFF);st->A->SetValue(0x12);st->X->SetValue(0x34);st->Y->SetValue(0x07);st->P->SetValue(0x04);
    *vs->SR=0x05;*vs->READ_BUFFER=0xBEEF;
    unsigned ar0=(u8)*vs->AR,r00=vs->R[0],r70=vs->R[7],sr0=*vs->SR,rb0=*vs->READ_BUFFER;
    unsigned sp0=st->S->GetValue(),p0=st->P->GetValue();u8 regs0[4]={(u8)st->A->GetValue(),(u8)st->X->GetValue(),(u8)st->Y->GetValue(),(u8)st->P->GetValue()};
    std::vector<u8> before(wram,wram+mem->GetWorkingRAMSize());unsigned mpr0[8];for(int i=0;i<8;++i)mpr0[i]=mem->GetMpr(i);
    gg_verify_transfer_abort_guard=preflight;gg_verify_block_write_guard=byte_guard;gg_verify_transfer_start_guard=start_guard;gg_verify_block_byte_observer=observe;
    std::vector<u8> frame(1024*512*4);std::vector<s16> audio(65536);GeargrafxCore::GG_Debug_Run debug={};bool halted=false;unsigned frames=0;
    while(frames<4&&!halted){int samples=0;core.RunToVBlank(frame.data(),audio.data(),&samples,&debug,false);++frames;halted=st->PC->GetValue()==bra_pc;}
    std::vector<u8> after(wram,wram+mem->GetWorkingRAMSize());std::vector<unsigned> changed,changed_values;
    for(unsigned i=0;i<before.size();++i)if(before[i]!=after[i]){changed.push_back(i);changed_values.push_back(after[i]);}
    unsigned destval[8];unsigned da=dst;for(unsigned i=0;i<len;++i){int ix=phys_wram(da);destval[i]=ix<0?0xFFFFFFFFu:after[ix];switch(op){case 0:case 4:da=(u16)(da+1);break;case 1:break;case 2:da=(u16)(da+((i&1)?-1:1));break;case 3:da=(u16)(da-1);break;}}
    std::fprintf(stderr,"{\"case\":\"%s\",\"status\":\"%s\",\"op\":%d,\"opcode\":%u,\"src\":%u,\"dst\":%u,\"length\":%u,\"preflight_calls\":%u,\"preflight_allow\":%u,\"preflight_deny\":%u,\"preflight_src\":[",name.c_str(),halted?"ok":"budget_exhausted",op,kOpcode[op],src,dst,len,g_pre_calls,g_pre_allow,g_pre_deny);
    for(size_t i=0;i<g_plan_src.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_plan_src[i]);std::fprintf(stderr,"],\"preflight_dst\":[");for(size_t i=0;i<g_plan_dst.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_plan_dst[i]);
    std::fprintf(stderr,"],\"src_bank\":[");for(size_t i=0;i<g_plan_src_bank.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_plan_src_bank[i]);std::fprintf(stderr,"],\"dst_bank\":[");for(size_t i=0;i<g_plan_dst_bank.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_plan_dst_bank[i]);
    std::fprintf(stderr,"],\"src_wram_index\":[");for(size_t i=0;i<g_plan_src_phys.size();++i)std::fprintf(stderr,"%s%d",i?",":"",g_plan_src_phys[i]);std::fprintf(stderr,"],\"dst_wram_index\":[");for(size_t i=0;i<g_plan_dst_phys.size();++i)std::fprintf(stderr,"%s%d",i?",":"",g_plan_dst_phys[i]);
    std::fprintf(stderr,"],\"byte_src\":[");for(size_t i=0;i<g_byte_src.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_byte_src[i]);std::fprintf(stderr,"],\"byte_dst\":[");for(size_t i=0;i<g_byte_dst.size();++i)std::fprintf(stderr,"%s%u",i?",":"",g_byte_dst[i]);
    std::fprintf(stderr,"],\"byte_guard_calls\":%u,\"start_calls\":%u,\"pc\":%u,\"sp_before\":%u,\"sp_after\":%u,\"p_before\":%u,\"p_after\":%u,\"a_before\":%u,\"a_after\":%u,\"x_before\":%u,\"x_after\":%u,\"y_before\":%u,\"y_after\":%u,\"preflight_cpu\":[%u,%u,%u,%u,%u],\"mpr_before\":[",g_byte_calls,g_start_calls,st->PC->GetValue(),sp0,st->S->GetValue(),p0,st->P->GetValue(),regs0[0],st->A->GetValue(),regs0[1],st->X->GetValue(),regs0[2],st->Y->GetValue(),g_pre_a,g_pre_x,g_pre_y,g_pre_p,g_pre_sp);
    for(int i=0;i<8;++i)std::fprintf(stderr,"%s%u",i?",":"",mpr0[i]);std::fprintf(stderr,"],\"mpr_after\":[");for(int i=0;i<8;++i)std::fprintf(stderr,"%s%u",i?",":"",mem->GetMpr(i));
    std::fprintf(stderr,"],\"dest_values\":[");for(unsigned i=0;i<len;++i)std::fprintf(stderr,"%s%u",i?",":"",destval[i]);std::fprintf(stderr,"],\"changed_wram\":[");for(size_t i=0;i<changed.size();++i)std::fprintf(stderr,"%s{\"index\":%u,\"value\":%u}",i?",":"",changed[i],changed_values[i]);
    std::fprintf(stderr,"],\"stack_wram_before\":[%u,%u,%u],\"stack_wram_after\":[%u,%u,%u],\"vdc_ar\":[%u,%u],\"vdc_r0\":[%u,%u],\"vdc_r7\":[%u,%u],\"vdc_sr\":[%u,%u],\"vdc_read_buffer\":[%u,%u]}",before[0x1FD],before[0x1FE],before[0x1FF],after[0x1FD],after[0x1FE],after[0x1FF],ar0,(u8)*vs->AR,r00,vs->R[0],r70,vs->R[7],sr0,*vs->SR,rb0,*vs->READ_BUFFER);
    return 0;
}
