// factory-g4: generic Geant4 application for Factory.
//   factory-g4 --gdml geometry.gdml [--physics FTFP_BERT_EMZ] [--sensitive A,B] [-t N] macro.mac
// Geometry: GDML. Source: GPS, set in the macro. Output: one CSV row per event in
// $FACTORY_OUTPUTS/events.csv: species, E_MeV, <volume>_keV for every sensitive volume
// (default: every volume placed directly in the world), back, escape.
//   back   = the primary last left the stack into the world moving towards -z
//   escape = the primary last left the stack into the world moving towards +z or sideways
// Both 0 = the primary stopped inside the stack.
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#include "G4Event.hh"
#include "G4GDMLParser.hh"
#include "G4GeneralParticleSource.hh"
#include "G4LogicalVolume.hh"
#include "G4PVPlacement.hh"
#include "G4PhysListFactory.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManagerFactory.hh"
#include "G4Step.hh"
#include "G4SystemOfUnits.hh"
#include "G4UImanager.hh"
#include "G4UserEventAction.hh"
#include "G4UserSteppingAction.hh"
#include "G4VUserActionInitialization.hh"
#include "G4VUserDetectorConstruction.hh"
#include "G4VUserPrimaryGeneratorAction.hh"

namespace {
std::vector<std::string> gNames;                       // sensitive volumes, in column order
std::map<const G4LogicalVolume*, int> gIndex;          // read-only once the run starts
std::ofstream gOut;
std::mutex gMutex;

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
  void BeginOfEventAction(const G4Event*) override { d_->dep.assign(gNames.size(), 0.0); d_->exit = 0; }
  void EndOfEventAction(const G4Event* ev) override {
    auto* p = ev->GetPrimaryVertex()->GetPrimary();
    std::ostringstream row;
    row << p->GetParticleDefinition()->GetParticleName() << ',' << p->GetKineticEnergy() / MeV;
    for (double d : d_->dep) row << ',' << d / keV;
    row << ',' << int(d_->exit == 1) << ',' << int(d_->exit == 2) << '\n';
    std::lock_guard<std::mutex> lock(gMutex);
    gOut << row.str();
  }
 private:
  EventData* d_;
};

class Actions : public G4VUserActionInitialization {
 public:
  void Build() const override {
    auto* d = new EventData;  // one per thread; lives for the process
    SetUserAction(new Gen);
    SetUserAction(new Evt(d));
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
  int threads = 0;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&]() -> std::string {
      if (i + 1 >= argc) { std::cerr << "factory-g4: " << a << " needs a value\n"; std::exit(2); }
      return argv[++i];
    };
    if (a == "--gdml") gdml = next();
    else if (a == "--physics") physics = next();
    else if (a == "--sensitive") sensitive = next();
    else if (a == "-t") threads = std::atoi(next().c_str());
    else if (!a.empty() && a[0] == '-') { std::cerr << "factory-g4: unknown option " << a << "\n"; return 2; }
    else macro = a;
  }
  if (gdml.empty()) { std::cerr << "usage: factory-g4 --gdml geometry.gdml [--physics LIST] [--sensitive A,B] [-t N] macro.mac\n"; return 2; }

  auto rm = std::unique_ptr<G4RunManager>(G4RunManagerFactory::CreateRunManager(
      threads > 0 ? G4RunManagerType::MT : G4RunManagerType::Serial));
  if (threads > 0) rm->SetNumberOfThreads(threads);
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
  if (!macro.empty()) G4UImanager::GetUIpointer()->ApplyCommand("/control/execute " + macro);
  gOut.flush();
  return 0;
}
