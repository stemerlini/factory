// factory-g4: generic Geant4 application for Factory.
//   factory-g4 --gdml geometry.gdml [--physics FTFP_BERT_EMZ] [--sensitive A,B] [--tracks N] [-t N] macro.mac
// Geometry: GDML. Source: GPS, set in the macro. Output: one CSV row per event in
// $FACTORY_OUTPUTS/events.csv: species, E_MeV, <volume>_keV for every sensitive volume
// (default: every volume placed directly in the world), back, escape.
//   back   = the primary last left the stack into the world moving towards -z
//   escape = the primary last left the stack into the world moving towards +z or sideways
// Both 0 = the primary stopped inside the stack.
// -t N worker threads (default: all cores). Rows are ordered by event ID within each run.
// --tracks N also writes tracks.csv (event,track,parent,particle,x,y,z in mm) for the first N
// events, for the 3D scene.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "G4Event.hh"
#include "G4EventManager.hh"
#include "G4GDMLParser.hh"
#include "G4GeneralParticleSource.hh"
#include "G4LogicalVolume.hh"
#include "G4PVPlacement.hh"
#include "G4PhysListFactory.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManagerFactory.hh"
#include "G4Step.hh"
#include "G4SystemOfUnits.hh"
#include "G4Threading.hh"
#include "G4UImanager.hh"
#include "G4Run.hh"
#include "G4UserEventAction.hh"
#include "G4UserRunAction.hh"
#include "G4UserSteppingAction.hh"
#include "G4VUserActionInitialization.hh"
#include "G4VUserDetectorConstruction.hh"
#include "G4VUserPrimaryGeneratorAction.hh"

namespace {
std::vector<std::string> gNames;                       // sensitive volumes, in column order
std::map<const G4LogicalVolume*, int> gIndex;          // read-only once the run starts
std::ofstream gOut, gTracks;
int gNTracks = 0;
std::mutex gMutex;
using Rows = std::pair<std::string, std::string>;
std::map<int, Rows> gPending;
size_t gPendingBytes = 0;
int gNext = 0;

// Geant4 assigns increasing event IDs to each worker. On overflow, each worker therefore
// produces one sorted stream; the master needs only its next ID to merge it at run end.
using File = std::unique_ptr<FILE, int (*)(FILE*)>;
std::map<int, File> gSpills;

void spill(int stream, int id, const Rows& rows) {
  auto [it, inserted] = gSpills.try_emplace(stream, nullptr, std::fclose);
  if (inserted) it->second.reset(std::tmpfile());
  auto* f = it->second.get();
  if (!f || std::fwrite(&id, sizeof(id), 1, f) != 1)
    throw std::runtime_error("factory-g4: cannot spool event ID");
  for (auto* s : {&rows.first, &rows.second}) {
    size_t size = s->size();
    if (std::fwrite(&size, sizeof(size), 1, f) != 1 || std::fwrite(s->data(), 1, size, f) != size)
      throw std::runtime_error("factory-g4: cannot spool event data");
  }
}

void submit(int id, Rows rows) {
  std::lock_guard<std::mutex> lock(gMutex);
  size_t bytes = rows.first.size() + rows.second.size();
  if (!gSpills.empty() || (id != gNext &&
      (gPending.size() >= 1024 || gPendingBytes + bytes > 1024 * 1024))) {
    for (auto& [key, pending] : gPending) spill(-1, key, pending);
    gPending.clear();
    spill(G4Threading::G4GetThreadId(), id, rows);
  } else if (id != gNext) {
    gPending.emplace(id, std::move(rows));
    gPendingBytes += bytes;
  } else {
    gOut << rows.first;
    if (gNTracks > 0) gTracks << rows.second;
    ++gNext;
    while (!gPending.empty() && gPending.begin()->first == gNext) {
      auto& [event, tracks] = gPending.begin()->second;
      gOut << event;
      if (gNTracks > 0) gTracks << tracks;
      gPendingBytes -= event.size() + tracks.size();
      gPending.erase(gPending.begin());
      ++gNext;
    }
  }
}

void finishOutput() {
  for (auto& [id, rows] : gPending) {
    gOut << rows.first;
    if (gNTracks > 0) gTracks << rows.second;
  }
  gPending.clear();
  std::map<int, FILE*> heads;
  auto advance = [&](FILE* f) {
    int id;
    if (std::fread(&id, sizeof(id), 1, f) == 1) heads.emplace(id, f);
    else if (std::ferror(f)) throw std::runtime_error("factory-g4: cannot read spooled event ID");
  };
  for (auto& [stream, file] : gSpills) {
    if (std::fseek(file.get(), 0, SEEK_SET)) throw std::runtime_error("factory-g4: cannot rewind spool");
    advance(file.get());
  }
  while (!heads.empty()) {
    auto* f = heads.begin()->second;
    heads.erase(heads.begin());
    for (auto* out : {&gOut, &gTracks}) {
      size_t size;
      if (std::fread(&size, sizeof(size), 1, f) != 1) throw std::runtime_error("factory-g4: truncated spool");
      char buf[8192];
      while (size) {
        auto n = std::min(sizeof(buf), size);
        if (std::fread(buf, 1, n, f) != n) throw std::runtime_error("factory-g4: truncated spool");
        out->write(buf, n);
        size -= n;
      }
    }
    advance(f);
  }
  gSpills.clear();
  gPendingBytes = 0;
  gNext = 0;
}

class Detector : public G4VUserDetectorConstruction {
 public:
  Detector(std::string file, std::vector<std::string> only) : file_(std::move(file)), only_(std::move(only)) {}
  G4VPhysicalVolume* Construct() override {
    G4GDMLParser parser;
    parser.Read(file_, false);
    auto* world = parser.GetWorldVolume();
    auto* wl = world->GetLogicalVolume();
    gNames.clear();
    gIndex.clear();
    auto add = [&](G4LogicalVolume* lv) {
      if (gIndex.count(lv)) return;
      gIndex[lv] = int(gNames.size());
      gNames.push_back(lv->GetName());
    };
    if (only_.empty()) {
      for (size_t i = 0; i < wl->GetNoDaughters(); ++i) add(wl->GetDaughter(i)->GetLogicalVolume());
    } else {
      for (auto& n : only_) {
        bool found = false;
        for (size_t i = 0; i < wl->GetNoDaughters(); ++i) {
          auto* lv = wl->GetDaughter(i)->GetLogicalVolume();
          if (lv->GetName() == n) { add(lv); found = true; }
        }
        if (!found) { std::cerr << "factory-g4: no volume '" << n << "' in the world\n"; std::exit(2); }
      }
    }
    return world;
  }
 private:
  std::string file_;
  std::vector<std::string> only_;
};

class Gen : public G4VUserPrimaryGeneratorAction {
 public:
  Gen() : gps_(new G4GeneralParticleSource) {}
  ~Gen() override { delete gps_; }
  void GeneratePrimaries(G4Event* ev) override { gps_->GeneratePrimaryVertex(ev); }
 private:
  G4GeneralParticleSource* gps_;
};

struct EventData {
  std::vector<double> dep;
  int exit = 0;  // 0 contained, 1 back, 2 escaped
  std::string tracks;
};

class Step : public G4UserSteppingAction {
 public:
  explicit Step(EventData* d) : d_(d) {}
  void UserSteppingAction(const G4Step* s) override {
    auto* pre = s->GetPreStepPoint()->GetPhysicalVolume();
    auto* post = s->GetPostStepPoint()->GetPhysicalVolume();
    auto* trk = s->GetTrack();
    double edep = s->GetTotalEnergyDeposit();
    if (pre && edep > 0) {
      auto it = gIndex.find(pre->GetLogicalVolume());
      if (it != gIndex.end()) d_->dep[it->second] += edep;
    }
    if (gNTracks > 0 && pre && G4EventManager::GetEventManager()->GetConstCurrentEvent()->GetEventID() < gNTracks) {
      std::ostringstream r;
      auto id = G4EventManager::GetEventManager()->GetConstCurrentEvent()->GetEventID();
      auto row = [&](const G4ThreeVector& p) {
        r << id << ',' << trk->GetTrackID() << ',' << trk->GetParentID() << ','
          << trk->GetDefinition()->GetParticleName() << ',' << p.x() / mm << ',' << p.y() / mm << ','
          << p.z() / mm << '\n';
      };
      if (trk->GetCurrentStepNumber() == 1) row(s->GetPreStepPoint()->GetPosition());
      row(s->GetPostStepPoint()->GetPosition());
      d_->tracks += r.str();
    }
    if (trk->GetParentID() != 0 || !pre) return;
    bool preStack = pre->GetMotherLogical() != nullptr;
    bool postWorld = !post || post->GetMotherLogical() == nullptr;
    if (preStack && postWorld)
      d_->exit = trk->GetMomentumDirection().z() < 0 ? 1 : 2;
    else if (!preStack && post && post->GetMotherLogical() != nullptr)
      d_->exit = 0;  // re-entered
  }
 private:
  EventData* d_;
};

class Evt : public G4UserEventAction {
 public:
  explicit Evt(EventData* d) : d_(d) {}
  void BeginOfEventAction(const G4Event*) override { d_->dep.assign(gNames.size(), 0.0); d_->exit = 0; d_->tracks.clear(); }
  void EndOfEventAction(const G4Event* ev) override {
    auto* p = ev->GetPrimaryVertex()->GetPrimary();
    std::ostringstream row;
    row << p->GetParticleDefinition()->GetParticleName() << ',' << p->GetKineticEnergy() / MeV;
    for (double d : d_->dep) row << ',' << d / keV;
    row << ',' << int(d_->exit == 1) << ',' << int(d_->exit == 2) << '\n';
    submit(ev->GetEventID(), {row.str(), std::move(d_->tracks)});
  }
 private:
  EventData* d_;
};

class Run : public G4UserRunAction {
 public:
  void EndOfRunAction(const G4Run*) override {
    if (!IsMaster()) return;
    std::lock_guard<std::mutex> lock(gMutex);
    finishOutput();
  }
};

class Actions : public G4VUserActionInitialization {
 public:
  void BuildForMaster() const override { SetUserAction(new Run); }
  void Build() const override {
    auto* d = new EventData;  // one per thread; lives for the process
    SetUserAction(new Gen);
    SetUserAction(new Evt(d));
    SetUserAction(new Run);
    SetUserAction(new Step(d));
  }
};

std::vector<std::string> split(const std::string& s) {
  std::vector<std::string> out;
  std::stringstream ss(s);
  for (std::string t; std::getline(ss, t, ',');) if (!t.empty()) out.push_back(t);
  return out;
}
}  // namespace

int main(int argc, char** argv) {
  std::string gdml, physics = "FTFP_BERT_EMZ", macro, sensitive;
  int threads = std::max(1u, std::thread::hardware_concurrency());
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&]() -> std::string {
      if (i + 1 >= argc) { std::cerr << "factory-g4: " << a << " needs a value\n"; std::exit(2); }
      return argv[++i];
    };
    if (a == "--gdml") gdml = next();
    else if (a == "--physics") physics = next();
    else if (a == "--sensitive") sensitive = next();
    else if (a == "--tracks") gNTracks = std::atoi(next().c_str());
    else if (a == "-t") threads = std::atoi(next().c_str());
    else if (!a.empty() && a[0] == '-') { std::cerr << "factory-g4: unknown option " << a << "\n"; return 2; }
    else macro = a;
  }
  if (gdml.empty()) { std::cerr << "usage: factory-g4 --gdml geometry.gdml [--physics LIST] [--sensitive A,B] [--tracks N] [-t N] macro.mac\n"; return 2; }

  if (threads < 1) { std::cerr << "factory-g4: -t needs at least 1 thread\n"; return 2; }
  auto rm = std::unique_ptr<G4RunManager>(G4RunManagerFactory::CreateRunManager(G4RunManagerType::MT));
  rm->SetNumberOfThreads(threads);
  G4PhysListFactory factory;
  if (!factory.IsReferencePhysList(physics)) { std::cerr << "factory-g4: unknown physics list " << physics << "\n"; return 2; }
  rm->SetUserInitialization(new Detector(gdml, split(sensitive)));
  rm->SetUserInitialization(factory.GetReferencePhysList(physics));
  rm->SetUserInitialization(new Actions);
  rm->Initialize();

  const char* out = std::getenv("FACTORY_OUTPUTS");
  gOut.open(std::string(out ? out : ".") + "/events.csv");
  gOut << "species,E_MeV";
  for (auto& n : gNames) gOut << ',' << n << "_keV";
  gOut << ",back,escape\n";
  if (gNTracks > 0) gTracks.open(std::string(out ? out : ".") + "/tracks.csv"), gTracks << "event,track,parent,particle,x,y,z\n";
  if (!macro.empty()) G4UImanager::GetUIpointer()->ApplyCommand("/control/execute " + macro);
  gOut.flush();
  gTracks.flush();
  if (!gOut || (gNTracks > 0 && !gTracks)) {
    std::cerr << "factory-g4: failed to write CSV output\n";
    return 1;
  }
  return 0;
}
